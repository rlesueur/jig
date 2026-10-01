"""Async client for any OpenAI-compatible chat endpoint (llama.cpp, Ollama, LM Studio, vLLM...).

Nothing here assumes a particular model. Reasoning text is optional: some
servers send ``reasoning_content`` or ``reasoning``, and many send neither.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import EndpointConfig
from .errors import ModelCapabilityError, ModelError, ModelServerUnavailable

log = logging.getLogger(__name__)

_REASONING_KEYS = ("reasoning_content", "reasoning")


def _reasoning(obj: dict[str, Any]) -> str:
    for key in _REASONING_KEYS:
        if isinstance(obj.get(key), str) and obj[key]:
            return obj[key]
    return ""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments_raw: str

    def arguments(self) -> dict[str, Any]:
        """Parse the JSON arguments. Raises ``ModelError`` if they are malformed."""
        if not self.arguments_raw.strip():
            return {}
        try:
            value = json.loads(self.arguments_raw)
        except json.JSONDecodeError as exc:
            raise ModelError(
                f"Tool call {self.name!r} has invalid JSON arguments: {exc}",
                body=self.arguments_raw,
            ) from exc
        if not isinstance(value, dict):
            raise ModelError(f"Tool call {self.name!r} arguments are not a JSON object")
        return value

    def as_message_part(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_raw},
        }


@dataclass
class ChatResult:
    content: str
    reasoning: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0

    def assistant_message(self) -> dict[str, Any]:
        """The message to append to history, in the standard OpenAI shape.
        Reasoning text is not part of that shape, so it is kept in the run records instead."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [tc.as_message_part() for tc in self.tool_calls]
        return msg

    def summary(self) -> dict[str, Any]:
        return {
            "finish_reason": self.finish_reason,
            "content_chars": len(self.content),
            "reasoning_chars": len(self.reasoning),
            "tool_calls": [tc.name for tc in self.tool_calls],
            "usage": self.usage,
            "elapsed_s": round(self.elapsed_s, 3),
        }


DeltaCallback = Callable[[str, str], Awaitable[None]]  # (kind, text) kind in reasoning|content


class ModelClient:
    def __init__(self, config: EndpointConfig, *, label: str = "model"):
        self.config = config
        self.label = label
        # Resolved by connect(): the configured name, or the one model the server reports.
        self.model_name: str | None = config.name or None
        self.server_info: dict[str, Any] = {}
        headers = {}
        if key := config.api_key:
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(
                connect=config.connect_timeout_s,
                read=config.read_timeout_s,
                write=30.0,
                pool=config.read_timeout_s,
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        """Query /v1/models, resolve the model name and confirm it is served. Fails loudly."""
        try:
            r = await self._client.get("/models", timeout=self.config.connect_timeout_s)
        except httpx.HTTPError as exc:
            raise ModelServerUnavailable(
                f"{self.label} server at {self.config.base_url} is unreachable: {exc!r}"
            ) from exc
        if r.status_code != 200:
            raise ModelServerUnavailable(
                f"{self.label} server {self.config.base_url}/models returned {r.status_code}: {r.text[:300]}"
            )
        try:
            entries = r.json().get("data") or []
        except ValueError as exc:
            raise ModelServerUnavailable(f"{self.label} server /models did not return JSON") from exc
        by_name: dict[str, dict[str, Any]] = {}
        for entry in entries:
            for name in [entry.get("id"), *(entry.get("aliases") or [])]:
                if name:
                    by_name[name] = entry
        ids = sorted({e.get("id") for e in entries if e.get("id")})
        if self.config.name:
            if self.config.name not in by_name:
                raise ModelServerUnavailable(
                    f"{self.label} {self.config.name!r} is not served at {self.config.base_url}; available: {ids}"
                )
            name = self.config.name
        elif len(ids) == 1:
            name = ids[0]
        else:
            raise ModelServerUnavailable(
                f"{self.label} name is not configured and {self.config.base_url} serves "
                f"{len(ids)} models ({ids}); set 'name' in the config"
            )
        self.model_name = name
        entry = by_name[name]
        self.server_info = {"model": name, "base_url": self.config.base_url, "context_tokens": _context_size(entry)}
        return {"status": "ok", **self.server_info}

    def _require_name(self, model: str | None) -> str:
        name = model or self.model_name
        if not name:
            raise ModelError(f"{self.label} name is unknown; call health() first or set 'name' in the config")
        return name

    def _body(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        stream: bool,
        model: str | None,
        max_tokens: int | None,
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            **self.config.sampling,
            "model": self._require_name(model),
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if tools:
            body["tools"] = tools
        if response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema, "strict": True},
            }
        return body

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        on_delta: DeltaCallback | None = None,
    ) -> ChatResult:
        """One chat completion. Streams internally when ``on_delta`` is given.

        Message content may be a list of OpenAI content parts, including ``image_url`` parts
        (see ``jig.vision.image_message``) for vision-capable models."""
        body = self._body(messages, tools=tools, stream=on_delta is not None, model=model,
                          max_tokens=max_tokens, response_schema=response_schema)
        started = time.perf_counter()
        try:
            if on_delta is None:
                result = await self._complete(body)
            else:
                result = await self._stream(body, on_delta)
        except httpx.TimeoutException as exc:
            raise ModelError(f"{self.label} request timed out: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise ModelServerUnavailable(f"{self.label} request failed: {exc!r}") from exc
        result.elapsed_s = time.perf_counter() - started
        if result.finish_reason == "length":
            raise ModelError(
                f"{self.label} output was cut off at max_tokens ({body['max_tokens']}); "
                "raise max_tokens or shorten the task"
            )
        return result

    async def _complete(self, body: dict[str, Any]) -> ChatResult:
        r = await self._client.post("/chat/completions", json=body)
        if r.status_code != 200:
            raise ModelError(f"{self.label} server returned HTTP {r.status_code}: {r.text[:500]}",
                             status=r.status_code, body=r.text)
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise ModelError(f"{self.label} response has no choices", body=r.text)
        msg = choices[0].get("message") or {}
        calls = [
            ToolCall(
                id=tc.get("id") or f"call_{i}",
                name=(tc.get("function") or {}).get("name", ""),
                arguments_raw=_arguments_text((tc.get("function") or {}).get("arguments")),
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        return ChatResult(
            content=msg.get("content") or "",
            reasoning=_reasoning(msg),
            tool_calls=calls,
            finish_reason=choices[0].get("finish_reason"),
            usage=data.get("usage") or {},
            timings=data.get("timings") or {},
        )

    async def _stream(self, body: dict[str, Any], on_delta: DeltaCallback) -> ChatResult:
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        async with self._client.stream("POST", "/chat/completions", json=body) as r:
            if r.status_code != 200:
                text = (await r.aread()).decode("utf-8", "replace")
                raise ModelError(f"{self.label} server returned HTTP {r.status_code}: {text[:500]}",
                                 status=r.status_code, body=text)
            async for line in self._sse_lines(r):
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ModelError(f"Malformed stream chunk: {exc}", body=line) from exc
                if "error" in chunk:
                    raise ModelError(f"{self.label} stream error: {chunk['error']}", body=line)
                usage = chunk.get("usage") or usage
                timings = chunk.get("timings") or timings
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if text := _reasoning(delta):
                        reasoning.append(text)
                        await on_delta("reasoning", text)
                    if text := delta.get("content"):
                        content.append(text)
                        await on_delta("content", text)
                    for pos, tc in enumerate(delta.get("tool_calls") or []):
                        slot = calls.setdefault(tc.get("index", pos), {"id": "", "name": "", "args": []})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"].append(_arguments_text(fn["arguments"]))
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        tool_calls = [
            ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments_raw="".join(s["args"]))
            for i, s in sorted(calls.items())
        ]
        return ChatResult(
            content="".join(content),
            reasoning="".join(reasoning),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            timings=timings,
        )

    @staticmethod
    async def _sse_lines(r: httpx.Response) -> AsyncIterator[str]:
        async for raw in r.aiter_lines():
            if raw.startswith("data:"):
                yield raw[5:].strip()

    # Capability checks ------------------------------------------------------
    async def probe_tool_calling(self) -> dict[str, Any]:
        """Ask for one specific tool call and verify it is well-formed. Raises ``ModelCapabilityError``."""
        tool = {
            "type": "function",
            "function": {
                "name": "record_probe",
                "description": "Record a probe word and number.",
                "parameters": {
                    "type": "object",
                    "properties": {"word": {"type": "string"}, "number": {"type": "integer"}},
                    "required": ["word", "number"],
                },
            },
        }
        messages = [
            {"role": "system", "content": "You are being tested for tool calling. Respond only by calling the tool."},
            {"role": "user", "content": "Call record_probe with word set to \"jig\" and number set to 42."},
        ]
        problem = f"{self.label} {self.model_name!r} at {self.config.base_url} failed the tool-calling check"
        try:
            result = await self.chat(messages, tools=[tool])
        except ModelError as exc:
            raise ModelCapabilityError(f"{problem}: the request failed: {exc}") from exc
        if not result.tool_calls:
            raise ModelCapabilityError(
                f"{problem}: it answered in text instead of calling the tool "
                f"(content: {result.content[:200]!r}). Jig needs a model and server with native tool calling."
            )
        call = result.tool_calls[0]
        if call.name != "record_probe":
            raise ModelCapabilityError(f"{problem}: it called an unknown tool {call.name!r}")
        try:
            args = call.arguments()
        except ModelError as exc:
            raise ModelCapabilityError(f"{problem}: malformed arguments {call.arguments_raw[:200]!r}") from exc
        if str(args.get("word", "")).strip().lower() != "jig" or args.get("number") != 42:
            raise ModelCapabilityError(f"{problem}: wrong arguments {args!r}")
        return {"tool_calling": True, "elapsed_s": round(result.elapsed_s, 2),
                "reasoning_reported": bool(result.reasoning)}

    async def probe_structured_output(self) -> dict[str, Any]:
        """Check JSON-schema structured output, which the planner and Sentinel rely on."""
        schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"],
                  "additionalProperties": False}
        messages = [{"role": "user", "content": "What is 2 + 3? Reply as JSON."}]
        problem = f"{self.label} {self.model_name!r} at {self.config.base_url} failed the structured-output check"
        try:
            result = await self.chat(messages, response_schema=schema)
        except ModelError as exc:
            raise ModelCapabilityError(
                f"{problem}: the server rejected or failed a JSON-schema response_format request: {exc}"
            ) from exc
        try:
            data = json.loads(result.content)
        except json.JSONDecodeError as exc:
            raise ModelCapabilityError(f"{problem}: output is not JSON: {result.content[:200]!r}") from exc
        if not isinstance(data, dict) or data.get("answer") != 5:
            raise ModelCapabilityError(f"{problem}: unexpected output {result.content[:200]!r}")
        return {"structured_output": True, "elapsed_s": round(result.elapsed_s, 2)}


def _arguments_text(value: Any) -> str:
    # Some servers send arguments as an already-parsed object rather than a JSON string.
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _context_size(entry: dict[str, Any]) -> int | None:
    """Context length if the server reports it (llama.cpp: meta.n_ctx; vLLM: max_model_len)."""
    meta = entry.get("meta") or {}
    for value in (meta.get("n_ctx"), entry.get("max_model_len"), entry.get("context_length")):
        if isinstance(value, int) and value > 0:
            return value
    return None
