"""Async client for any OpenAI-compatible chat endpoint (llama.cpp, Ollama, LM Studio, vLLM, or a cloud API).

Nothing here assumes a particular model. Reasoning text is optional: some
servers send ``reasoning_content`` or ``reasoning``, and many send neither.
Documented provider differences (``jig.endpoints.PROVIDERS``) are applied
explicitly: the output-limit field, how structured output is requested, and
the fields a provider needs back in the conversation (Gemini's tool-call thought
signatures, OpenRouter's ``reasoning_details``), which are kept verbatim.

The API key is never logged and is redacted from every error message.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import EndpointConfig
from .errors import ConfigError, ModelCapabilityError, ModelError, ModelServerUnavailable

log = logging.getLogger(__name__)

_REASONING_KEYS = ("reasoning_content", "reasoning")
_TOOL_CALL_KEYS = {"index", "id", "type", "function"}
RESPOND_TOOL = "respond"
_RESPOND_INSTRUCTION = (f"Give your answer only by calling the {RESPOND_TOOL} tool: its arguments are your whole "
                        "answer. Do not reply with text.")


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
    # Any other fields the server sent with the call (for example Gemini's extra_content.google.thought_signature),
    # sent back unchanged in the conversation history.
    extra: dict[str, Any] = field(default_factory=dict)

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
            **self.extra,
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
    # OpenRouter's structured reasoning blocks, which it asks to be passed back unmodified.
    reasoning_details: list[dict[str, Any]] = field(default_factory=list)
    # Gemini's vendor extension on the message (extra_content.google...), passed back unmodified.
    extra_content: dict[str, Any] = field(default_factory=dict)

    def assistant_message(self) -> dict[str, Any]:
        """The message to append to history, in the standard OpenAI shape.
        Reasoning text is not part of that shape, so it is kept in the run records instead."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [tc.as_message_part() for tc in self.tool_calls]
        if self.reasoning_details:
            msg["reasoning_details"] = self.reasoning_details
        if self.extra_content:
            msg["extra_content"] = self.extra_content
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
    def __init__(self, config: EndpointConfig, *, label: str = "model", api_key: str | None = None):
        """``api_key`` is the resolved key (``jig.cloud.resolve_api_key``); without it, ``api_key_env`` is read.
        An endpoint with ``api_key_secret`` needs the key passed in, because only the vault can provide it."""
        self.config = config
        self.label = label
        # Resolved by connect(): the configured name, or the one model the server reports.
        self.model_name: str | None = config.name or None
        self.server_info: dict[str, Any] = {}
        provider = config.provider_info
        headers = {**(provider.headers if provider else {}), **config.headers}
        if api_key is None:
            if config.api_key_secret:
                raise ConfigError(f"{label}: the API key is in the vault ({config.api_key_secret!r}) and was not "
                                  "resolved; use jig.cloud.resolve_api_key")
            api_key = config.api_key
        self._key = api_key or ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            verify=ssl.create_default_context(cafile=config.ca_file) if config.ca_file else True,
            timeout=httpx.Timeout(
                connect=config.connect_timeout_s,
                read=config.read_timeout_s,
                write=30.0,
                pool=config.read_timeout_s,
            ),
        )

    def redact(self, text: str) -> str:
        """Remove the API key, and any masked form of it a provider echoes back (``sk-abcd****wxyz``)."""
        key = self._key
        if not key or not text:
            return text
        text = text.replace(key, "[api key]")
        if len(key) >= 16:
            text = re.sub(re.escape(key[:8]) + r"[^\s\"',]*", "[api key]", text)
        return text

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        """Query /v1/models, resolve the model name and confirm it is served. Fails loudly."""
        provider = self.config.provider_info
        try:
            r = await self._client.get("/models", params=provider.models_params if provider else None,
                                       timeout=self.config.connect_timeout_s)
        except httpx.HTTPError as exc:
            raise ModelServerUnavailable(self.redact(
                f"{self.label} server at {self.config.base_url} is unreachable: {exc!r}"
            ), reason="unreachable") from exc
        if r.status_code != 200:
            refused = r.status_code in (401, 403) and bool(self._key or self.config.location.is_cloud)
            hint = " (the API key was refused: check it with 'jig model key status')" if refused else ""
            raise ModelServerUnavailable(self.redact(
                f"{self.label} server {self.config.base_url}/models returned {r.status_code}{hint}: {r.text[:300]}"
            ), reason="key_refused" if refused else "http_error", status=r.status_code)
        try:
            entries = r.json().get("data") or []
        except (ValueError, AttributeError) as exc:
            raise ModelServerUnavailable(f"{self.label} server /models did not return JSON",
                                         reason="not_json") from exc
        by_name: dict[str, dict[str, Any]] = {}
        prefixes = provider.model_id_prefixes if provider else ()
        for entry in entries:
            for name in [entry.get("id"), *(entry.get("aliases") or [])]:
                if name:
                    by_name[name] = entry
                    for prefix in prefixes:
                        if name.startswith(prefix):
                            by_name.setdefault(name[len(prefix):], entry)
        ids = sorted({e.get("id") for e in entries if e.get("id")})
        if self.config.name:
            if self.config.name not in by_name:
                raise ModelServerUnavailable(
                    f"{self.label} {self.config.name!r} is not served at {self.config.base_url}; available: {ids}",
                    reason="not_served", available=ids)
            name = self.config.name
        elif len(ids) == 1:
            name = ids[0]
        else:
            raise ModelServerUnavailable(
                f"{self.label} name is not configured and {self.config.base_url} serves "
                f"{len(ids)} models ({ids}); set 'name' in the config", reason="which_model", available=ids)
        self.model_name = name
        entry = by_name[name]
        self.server_info = {"model": name, "base_url": self.config.base_url, "context_tokens": _context_size(entry),
                            "location": self.config.location.kind}
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
        provider = self.config.provider_info
        body: dict[str, Any] = {
            **self.config.sampling,
            "model": self._require_name(model),
            "messages": messages,
            "stream": stream,
        }
        if (limit := max_tokens or self.config.max_tokens) is not None:
            body[provider.max_tokens_field if provider else "max_tokens"] = limit
        if tools:
            body["tools"] = tools
        if response_schema is not None:
            if self.config.structured_output_mode == "tool_call":
                if tools:
                    raise ModelError(f"{self.label}: structured output as a tool call cannot be combined with tools")
                # Not forced with tool_choice: current Claude models reject forced tool use (HTTP 400). The reply
                # must be exactly this call, which _structured() checks.
                body["messages"] = _with_instruction(messages, _RESPOND_INSTRUCTION)
                body["tools"] = [{"type": "function", "function": {
                    "name": RESPOND_TOOL, "description": "Give your answer. The arguments are the whole answer.",
                    "parameters": response_schema}}]
            else:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": response_schema, "strict": True},
                }
        return body

    def _structured(self, result: ChatResult) -> ChatResult:
        """In tool_call mode, the answer is the arguments of the one ``respond`` call; anything else is an error."""
        calls = result.tool_calls
        if len(calls) != 1 or calls[0].name != RESPOND_TOOL:
            raise ModelError(
                f"{self.label} did not answer with the single {RESPOND_TOOL!r} tool call that structured output "
                f"needs (structured_output = \"tool_call\"); it sent {[c.name for c in calls]} and text "
                f"{result.content[:200]!r}", body=result.content)
        result.content, result.tool_calls = calls[0].arguments_raw, []
        return result

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
            raise ModelError(self.redact(f"{self.label} request timed out: {exc!r}")) from exc
        except httpx.HTTPError as exc:
            raise ModelServerUnavailable(self.redact(f"{self.label} request failed: {exc!r}"),
                                         reason="unreachable") from exc
        result.elapsed_s = time.perf_counter() - started
        if result.finish_reason == "length":
            raise ModelError(self._cut_off_message(body, result))
        if response_schema is not None and self.config.structured_output_mode == "tool_call":
            result = self._structured(result)
        return result

    def _cut_off_message(self, body: dict[str, Any], result: ChatResult) -> str:
        """Why the reply stopped early (finish_reason "length"): the configured output limit, or the context window."""
        used = _token_counts(result)
        field_name = next((k for k in ("max_completion_tokens", "max_tokens") if k in body), None)
        if field_name is not None:
            return (f"{self.label} output was cut off at the output limit set in the config ({field_name} = "
                    f"{body[field_name]}{used}); raise or remove max_tokens"
                    + (", up to the model's documented maximum" if self.config.provider_info
                       and self.config.provider_info.max_tokens_required else "")
                    + ". Reasoning models' thinking counts towards it.")
        context = self.server_info.get("context_tokens")
        size = f" of {context} tokens" if context else ""
        return (f"{self.label} output stopped because the conversation filled the model's context window{size}{used}. "
                "Start the model server with a larger context, or split the task into smaller ones.")

    def _http_error(self, status: int, text: str) -> ModelError:
        text = self.redact(text)
        hint = " (the API key was refused: check it with 'jig model key status')" if status in (401, 403) else ""
        if overflow := _context_overflow(text):
            return ModelError(f"{self.label}: the conversation ({overflow[0]} tokens) no longer fits the model's "
                              f"context window ({overflow[1]} tokens), so the server refused it. Start the model "
                              "server with a larger context, or split the task into smaller ones.",
                              status=status, body=text)
        return ModelError(f"{self.label} server returned HTTP {status}{hint}: {text[:500]}", status=status, body=text)

    async def _complete(self, body: dict[str, Any]) -> ChatResult:
        r = await self._client.post("/chat/completions", json=body)
        if r.status_code != 200:
            raise self._http_error(r.status_code, r.text)
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise ModelError(f"{self.label} response has no choices", body=self.redact(r.text))
        msg = choices[0].get("message") or {}
        calls = [
            ToolCall(
                id=tc.get("id") or f"call_{i}",
                name=(tc.get("function") or {}).get("name", ""),
                arguments_raw=_arguments_text((tc.get("function") or {}).get("arguments")),
                extra={k: v for k, v in tc.items() if k not in _TOOL_CALL_KEYS},
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        details = msg.get("reasoning_details")
        return ChatResult(
            content=msg.get("content") or "",
            reasoning=_reasoning(msg),
            tool_calls=calls,
            finish_reason=choices[0].get("finish_reason"),
            usage=data.get("usage") or {},
            timings=data.get("timings") or {},
            reasoning_details=details if isinstance(details, list) else [],
            extra_content=msg["extra_content"] if isinstance(msg.get("extra_content"), dict) else {},
        )

    async def _stream(self, body: dict[str, Any], on_delta: DeltaCallback) -> ChatResult:
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        details: list[dict[str, Any]] = []
        extra_content: dict[str, Any] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        async with self._client.stream("POST", "/chat/completions", json=body) as r:
            if r.status_code != 200:
                raise self._http_error(r.status_code, (await r.aread()).decode("utf-8", "replace"))
            async for line in self._sse_lines(r):
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ModelError(f"Malformed stream chunk: {exc}", body=self.redact(line)) from exc
                if "error" in chunk:
                    raise ModelError(self.redact(f"{self.label} stream error: {chunk['error']}"),
                                     body=self.redact(line))
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
                    if isinstance(delta.get("reasoning_details"), list):
                        _merge_details(details, delta["reasoning_details"])
                    if isinstance(delta.get("extra_content"), dict):
                        extra_content.update(delta["extra_content"])
                    for pos, tc in enumerate(delta.get("tool_calls") or []):
                        slot = calls.setdefault(tc.get("index", pos), {"id": "", "name": "", "args": [], "extra": {}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"].append(_arguments_text(fn["arguments"]))
                        slot["extra"].update({k: v for k, v in tc.items() if k not in _TOOL_CALL_KEYS})
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        tool_calls = [
            ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments_raw="".join(s["args"]), extra=s["extra"])
            for i, s in sorted(calls.items())
        ]
        return ChatResult(
            content="".join(content),
            reasoning="".join(reasoning),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            timings=timings,
            reasoning_details=details,
            extra_content=extra_content,
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
            how = ("a structured answer as a tool call" if self.config.structured_output_mode == "tool_call"
                   else "a JSON-schema response_format request")
            raise ModelCapabilityError(f"{problem}: the server rejected or failed {how}: {exc}") from exc
        try:
            data = json.loads(result.content)
        except json.JSONDecodeError as exc:
            raise ModelCapabilityError(f"{problem}: output is not JSON: {result.content[:200]!r}") from exc
        if not isinstance(data, dict) or data.get("answer") != 5:
            raise ModelCapabilityError(f"{problem}: unexpected output {result.content[:200]!r}")
        return {"structured_output": True, "elapsed_s": round(result.elapsed_s, 2)}


def _with_instruction(messages: list[dict[str, Any]], instruction: str) -> list[dict[str, Any]]:
    """Add an instruction to the leading system message (some chat templates allow only one, at the start)."""
    if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
        return [{**messages[0], "content": f"{messages[0]['content']}\n\n{instruction}"}, *messages[1:]]
    return [{"role": "system", "content": instruction}, *messages]


def _merge_details(details: list[dict[str, Any]], chunk: list[Any]) -> None:
    """Merge streamed reasoning_details by ``index``: text pieces are joined, other fields kept as last sent."""
    for part in chunk:
        if not isinstance(part, dict):
            continue
        index = part.get("index")
        slot = next((d for d in details if index is not None and d.get("index") == index), None)
        if slot is None:
            details.append(dict(part))
            continue
        for key, value in part.items():
            if key in ("text", "summary", "data") and isinstance(value, str) and isinstance(slot.get(key), str):
                slot[key] += value
            elif value is not None:
                slot[key] = value


def _token_counts(result: ChatResult) -> str:
    """", prompt N tokens, output M tokens" from the usage or llama.cpp timings the server reported, or ""."""
    prompt = result.usage.get("prompt_tokens") or result.timings.get("prompt_n")
    output = result.usage.get("completion_tokens") or result.timings.get("predicted_n")
    parts = [f"prompt {prompt} tokens" if prompt else "", f"output {output} tokens" if output else ""]
    return "".join(f", {p}" for p in parts if p)


def _context_overflow(text: str) -> tuple[int, int] | None:
    """(prompt tokens, context size) from llama.cpp's exceed_context_size_error, which reports both."""
    try:
        error = json.loads(text).get("error")
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict) or error.get("type") != "exceed_context_size_error":
        return None
    prompt, context = error.get("n_prompt_tokens"), error.get("n_ctx")
    return (prompt, context) if isinstance(prompt, int) and isinstance(context, int) else None


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
