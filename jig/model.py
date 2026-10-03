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

import asyncio
import json
import logging
import random
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import schema as json_schema
from .config import EndpointConfig
from .errors import (ConfigError, ModelCapabilityError, ModelError, ModelServerUnavailable, ModelStalled,
                     ModelStopped)
from .progress import ProgressCheck, Stop, requested_repeats

log = logging.getLogger(__name__)

_REASONING_KEYS = ("reasoning_content", "reasoning")
_TOOL_CALL_KEYS = {"index", "id", "type", "function"}
RESPOND_TOOL = "respond"
_RESPOND_INSTRUCTION = (f"Give your answer only by calling the {RESPOND_TOOL} tool: its arguments are your whole "
                        "answer. Do not reply with text.")
# A structured answer that is not valid is sent back once, saying what was wrong. A second invalid answer fails.
STRUCTURED_RETRIES = 1
# After an answer stopped for repeating itself, the fresh request asks for a new seed and this much more
# temperature (standard OpenAI-compatible fields; not sent where the provider does not take them).
RETRY_TEMPERATURE_STEP = 0.2
# Asks a streamed reply to end with its token counts ({"include_usage": true}): the standard field, documented
# by OpenAI, Anthropic's and Gemini's OpenAI-compatible APIs, llama.cpp, vLLM, Ollama and LM Studio. A server
# that refuses it is asked again without it, and its replies say why they have no counts (usage_missing).
USAGE_FIELD = "stream_options"
# Keys Jig keeps on saved messages for itself; they are never sent to a model.
JIG_ONLY_KEYS = ("jig_stopped", "jig_continue")
# llama.cpp's server answers HTTP 500 with one of these when its chat parser cannot read the model's output as
# the tool call it was asked for ("Failed to parse input at pos" up to about b8700; the PEG parser after that).
_UNPARSED = re.compile(r"The model produced output that does not match the expected|Failed to parse input at pos")


@dataclass(frozen=True)
class _Refusal:
    """Why a structured answer was not accepted. ``kind`` is content-free and may be logged and recorded;
    ``detail`` is told to the model and put in the final error, and may name properties from the answer."""

    kind: str  # "cut_off", "unparsed", "no_call", "wrong_calls", "invalid_json", "schema" or "repetition"
    detail: str
    # For the user, if the last attempt fails this way too (how to stop answers being cut off).
    explanation: str = ""
    # For "repetition": where Jig stopped the stream, and why (content-free).
    stop: Stop | None = None


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
    # For a structured answer: why each earlier answer was sent back (_Refusal.kind), oldest first.
    structured_retries: list[str] = field(default_factory=list)
    # Each attempt Jig stopped as it streamed (jig.progress.Stop.record, with its attempt number), and the seed
    # and temperature each fresh retry asked for, with any it could not send and why. Content-free.
    stops: list[dict[str, Any]] = field(default_factory=list)
    retry_sampling: list[dict[str, Any]] = field(default_factory=list)
    # Why the reply has no token counts, when it has none (the server refused stream_options, or sent none).
    usage_missing: str | None = None

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
            **({"structured_retries": self.structured_retries} if self.structured_retries else {}),
            **({"stops": self.stops} if self.stops else {}),
            **({"retry_sampling": self.retry_sampling} if self.retry_sampling else {}),
            **({"usage_missing": self.usage_missing} if self.usage_missing else {}),
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
        # Reading has no timeout of its own: _stream gives up only on a server that has gone silent.
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            verify=ssl.create_default_context(cafile=config.ca_file) if config.ca_file else True,
            timeout=httpx.Timeout(
                connect=config.connect_timeout_s,
                read=None,
                write=30.0,
                pool=config.first_token_timeout_s,
            ),
        )
        # Request fields this server refused on a retry (field -> why), so they are not sent again.
        self._refused_fields: dict[str, str] = {}

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
        messages = [{k: v for k, v in m.items() if k not in JIG_ONLY_KEYS} if any(k in m for k in JIG_ONLY_KEYS)
                    else m for m in messages]
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
            json_schema.check_schema(response_schema)
            if self.config.structured_output_mode == "tool_call":
                if tools:
                    raise ModelError(f"{self.label}: structured output as a tool call cannot be combined with tools")
                body["messages"] = _with_instruction(messages, _RESPOND_INSTRUCTION)
                body["tools"] = [{"type": "function", "function": {
                    "name": RESPOND_TOOL, "description": "Give your answer. The arguments are the whole answer.",
                    "parameters": response_schema}}]
                if not provider or provider.force_tool_call:
                    body["tool_choice"] = "required"
                    body["parallel_tool_calls"] = False
            else:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": response_schema, "strict": True},
                }
        return body

    def _refusal(self, body: dict[str, Any], result: ChatResult, schema: dict[str, Any]) -> _Refusal | None:
        """Why a structured answer cannot be accepted, or None. In tool_call mode the answer is the arguments of
        exactly one ``respond`` call."""
        if result.finish_reason == "length":
            used = _token_counts(result).removeprefix(", ")
            return _Refusal("cut_off", "the answer was cut off before it was complete" + (f" ({used})" if used else ""),
                            explanation=self._cut_off_message(body, result))
        if self.config.structured_output_mode == "tool_call":
            calls = result.tool_calls
            if not calls:
                return _Refusal("no_call", f"the answer was text ({len(result.content)} characters), not a call to the "
                                           f"{RESPOND_TOOL} tool")
            if len(calls) != 1 or calls[0].name != RESPOND_TOOL:
                return _Refusal("wrong_calls", f"there were {len(calls)} tool calls "
                                               f"({', '.join(c.name or '?' for c in calls)}), not exactly one call to "
                                               f"{RESPOND_TOOL}")
            text = calls[0].arguments_raw
        else:
            text = result.content
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            return _Refusal("invalid_json", f"the answer is not valid JSON ({exc.msg} at character {exc.pos})")
        if found := json_schema.problems(value, schema):
            return _Refusal("schema", "the answer does not match the schema: " + "; ".join(found))
        return None

    def _correction(self, result: ChatResult | None, refusal: _Refusal) -> list[dict[str, Any]]:
        """The messages that send a refused answer back: the answer as given, then exactly what was wrong.
        An answer that was cut off (it can fill the context window) or that the server could not read is not
        repeated: the model is told what happened to it."""
        tool_call = self.config.structured_output_mode == "tool_call"
        again = (f" Call the {RESPOND_TOOL} tool once, with arguments that match its schema." if tool_call
                 else " Answer again with only the JSON object, matching the schema.")
        if refusal.kind == "repetition":
            why = ("you kept writing after the answer was complete" if refusal.stop
                   and refusal.stop.reason == "answer_complete" else "you were repeating yourself")
            return [{"role": "user", "content": f"Your previous answer to this was stopped because {why}. "
                                                f"Answer once, then stop.{again}"}]
        if result is None or refusal.kind == "cut_off":
            return [{"role": "user", "content": f"Your previous answer to this was not accepted: {refusal.detail}. "
                                                f"Keep your thinking short.{again}"}]
        say = f"Not accepted: {refusal.detail}.{again}"
        if tool_call and result.tool_calls:
            return [result.assistant_message(),
                    *({"role": "tool", "tool_call_id": c.id, "content": say} for c in result.tool_calls)]
        return [result.assistant_message(), {"role": "user", "content": say}]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        on_delta: DeltaCallback | None = None,
        relaxed: bool = False,
    ) -> ChatResult:
        """One chat completion. Every request streams; ``on_delta`` is given each piece as it arrives.

        Message content may be a list of OpenAI content parts, including ``image_url`` parts
        (see ``jig.vision.image_message``) for vision-capable models.

        Jig's progress check (``jig.progress``) watches the stream. A reply that is repeating itself is stopped
        (the request is cancelled) and ``ModelStopped`` raised; ``relaxed`` (the user chose to continue such a
        reply) stops only a very long exact repeat. A server that goes silent raises ``ModelStalled``.

        With ``response_schema``, the answer (``content``) is JSON checked against the schema. An answer that is
        not, that was cut off, that the server could not read, or that Jig stopped for repeating itself is sent
        back once saying what was wrong (``STRUCTURED_RETRIES``) and recorded in ``structured_retries``; if the
        next answer is not valid either, ``ModelError`` is raised."""
        body = self._body(messages, tools=tools, stream=True, model=model, max_tokens=max_tokens,
                          response_schema=response_schema)
        requested = requested_repeats(messages)
        if response_schema is None:
            result = await self._send(body, on_delta, ProgressCheck(relaxed=relaxed, requested=requested))
            if result.finish_reason == "length":
                raise ModelError(self._cut_off_message(body, result))
            return result
        first = await self._attempt(body, on_delta, self._check(requested))
        return await self._settle(body, response_schema, first, on_delta, requested=requested)

    def _check(self, requested: int | None) -> ProgressCheck:
        """The progress check for one structured request, which knows where the answer is: the respond call's
        arguments, or the text."""
        answer = "tool_arguments" if self.config.structured_output_mode == "tool_call" else "content"
        return ProgressCheck(answer=answer, requested=requested)

    async def _attempt(self, body: dict[str, Any], on_delta: DeltaCallback | None,
                       check: ProgressCheck | None = None) -> ChatResult | _Refusal:
        """One structured request. A server that could not read the output as the tool call it was asked for
        (llama.cpp's HTTP 500) has received an invalid answer, like any other; so has an answer Jig stopped for
        repeating itself."""
        try:
            return await self._send(body, on_delta, check)
        except ModelStopped as exc:
            return _Refusal("repetition", exc.stop.describe(), stop=exc.stop)
        except ModelError as exc:
            if (self.config.structured_output_mode == "tool_call" and exc.status == 500
                    and _UNPARSED.search(exc.body or "")):
                return _Refusal("unparsed", f"the server could not read the answer as a call to the {RESPOND_TOOL} "
                                            f"tool (HTTP 500: {_error_message(exc.body or '')})")
            raise

    async def _settle(self, body: dict[str, Any], schema: dict[str, Any], result: ChatResult | _Refusal,
                      on_delta: DeltaCallback | None = None, *, requested: int | None = None) -> ChatResult:
        """Accept a structured answer, or send it back once (``STRUCTURED_RETRIES``) and accept the next one.
        An answer stopped for repeating itself is not sent back: the request is made afresh with a correction that
        does not quote it, a new seed and a little more temperature (``_fresh_sampling``)."""
        refusals: list[_Refusal] = []
        stops: list[dict[str, Any]] = []
        sampling: list[dict[str, Any]] = []
        first_messages = body["messages"]
        elapsed = 0.0
        while True:
            answer = result if isinstance(result, ChatResult) else None
            if answer is None:
                refusal = result
            else:
                elapsed += answer.elapsed_s
                if (refusal := self._refusal(body, answer, schema)) is None:
                    if self.config.structured_output_mode == "tool_call":
                        answer.content, answer.tool_calls = answer.tool_calls[0].arguments_raw, []
                    answer.elapsed_s = elapsed
                    answer.structured_retries = [r.kind for r in refusals]
                    answer.stops, answer.retry_sampling = stops, sampling
                    return answer
            refusals.append(refusal)
            if refusal.stop is not None:
                stops.append({"attempt": len(refusals), **refusal.stop.record()})
            if len(refusals) > STRUCTURED_RETRIES:
                raise ModelError(
                    f"{self.label} gave no valid structured answer in {len(refusals)} attempts; Jig sent each one "
                    "before the last back, saying what was wrong. "
                    + " ".join(f"Attempt {i}: {r.detail}." for i, r in enumerate(refusals, 1))
                    + (f" {refusal.explanation}" if refusal.explanation else ""),
                    body=self.redact(answer.content) if answer is not None else None,
                    record={"structured_retries": [r.kind for r in refusals], "stops": stops,
                            "retry_sampling": sampling})
            log.warning("%s: structured answer not accepted (%s); sending it back, retry %d of %d", self.label,
                        refusal.kind, len(refusals), STRUCTURED_RETRIES)
            if refusal.kind == "repetition":
                extras, note = self._fresh_sampling(body, attempt=len(refusals) + 1)
                sampling.append(note)
                body = {**body, "messages": [*first_messages, *self._correction(None, refusal)]}
                result = await self._attempt_with(body, extras, note, on_delta, requested)
            else:
                body = {**body, "messages": [*body["messages"], *self._correction(answer, refusal)]}
                result = await self._attempt(body, on_delta, self._check(requested))

    def _fresh_sampling(self, body: dict[str, Any], *, attempt: int) -> tuple[dict[str, Any], dict[str, Any]]:
        """The request fields for a fresh retry, a new ``seed`` and ``temperature`` raised by
        RETRY_TEMPERATURE_STEP, and a record of them with any field not sent and why."""
        provider = self.config.provider_info
        note: dict[str, Any] = {"attempt": attempt, "not_sent": {}}
        extras: dict[str, Any] = {}

        def allowed(key: str) -> bool:
            if key in self._refused_fields:
                note["not_sent"][key] = self._refused_fields[key]
                return False
            if provider and provider.sampling_keys is not None and key not in provider.sampling_keys:
                note["not_sent"][key] = f"{provider.label} does not document it"
                return False
            return True

        if allowed("seed"):
            extras["seed"] = note["seed"] = random.SystemRandom().randrange(1, 2**31)
        base = body.get("temperature")
        if not isinstance(base, (int, float)) or isinstance(base, bool):
            note["not_sent"]["temperature"] = "no temperature is configured, so the server's own default is not known"
        else:
            top = provider.temperature_max if provider and provider.temperature_max is not None else 2.0
            raised = round(min(base + RETRY_TEMPERATURE_STEP, top), 2)
            if raised <= base:
                note["not_sent"]["temperature"] = f"already at the highest allowed ({top})"
            elif allowed("temperature"):
                extras["temperature"] = note["temperature"] = raised
                note["temperature_was"] = base
        if not note["not_sent"]:
            del note["not_sent"]
        return extras, note

    async def _attempt_with(self, body: dict[str, Any], extras: dict[str, Any], note: dict[str, Any],
                            on_delta: DeltaCallback | None, requested: int | None) -> ChatResult | _Refusal:
        """A fresh attempt with the retry's sampling fields. A server that refuses the request because of them
        (HTTP 400 or 422) is asked once more without them, and that is recorded."""
        try:
            return await self._attempt({**body, **extras}, on_delta, self._check(requested))
        except ModelError as exc:
            if not extras or exc.status not in (400, 422):
                raise
            named = [k for k in extras if k in (exc.body or "")] or list(extras)
            for key in named:
                self._refused_fields[key] = f"the server refused it (HTTP {exc.status})"
                note.setdefault("not_sent", {})[key] = self._refused_fields[key]
                note.pop(key, None)
                note.pop(f"{key}_was", None)
            log.warning("%s: the server refused %s on a retry (HTTP %s); asking again without", self.label,
                        ", ".join(named), exc.status)
            return await self._attempt({**body, **{k: v for k, v in extras.items() if k not in named}}, on_delta,
                                       self._check(requested))

    async def _send(self, body: dict[str, Any], on_delta: DeltaCallback | None,
                    check: ProgressCheck | None = None) -> ChatResult:
        started = time.perf_counter()
        check = check or ProgressCheck()
        if body.get("stream") and USAGE_FIELD not in self._refused_fields:
            body = {**body, USAGE_FIELD: {"include_usage": True}}
        try:
            try:
                result = await self._stream(body, on_delta, check, started)
            except ModelError as exc:
                if USAGE_FIELD not in body or exc.status not in (400, 422) or USAGE_FIELD not in (exc.body or ""):
                    raise
                # Asking for token counts is not worth failing the reply: ask again without, and say so.
                self._refused_fields[USAGE_FIELD] = f"the server refused it (HTTP {exc.status})"
                log.warning("%s: the server refused %s (HTTP %s), so its replies will have no token counts",
                            self.label, USAGE_FIELD, exc.status)
                body = {k: v for k, v in body.items() if k != USAGE_FIELD}
                result = await self._stream(body, on_delta, check, started)
        except httpx.TimeoutException as exc:
            raise ModelError(self.redact(f"{self.label} request timed out: {exc!r}")) from exc
        except httpx.HTTPError as exc:
            raise ModelServerUnavailable(self.redact(f"{self.label} request failed: {exc!r}"),
                                         reason="unreachable") from exc
        result.elapsed_s = time.perf_counter() - started
        if not result.usage:
            result.usage_missing = (f"{USAGE_FIELD} was not sent: {self._refused_fields[USAGE_FIELD]}"
                                    if USAGE_FIELD in self._refused_fields else "the server sent no usage")
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

    def _stream_error(self, chunk: dict[str, Any], line: str) -> ModelError:
        """An error sent inside a stream that had already begun (HTTP 200). Its own code is kept as the status:
        llama.cpp reports output its parser cannot read this way, with code 500, once the reply is streaming."""
        error = chunk["error"]
        code = error.get("code") if isinstance(error, dict) else None
        return ModelError(self.redact(f"{self.label} stream error: {error}"), body=self.redact(line),
                          status=code if isinstance(code, int) and not isinstance(code, bool) else None)

    async def _stream(self, body: dict[str, Any], on_delta: DeltaCallback | None, check: ProgressCheck,
                      started: float) -> ChatResult:
        """Read one streamed reply, piece by piece, through the progress check. Stopping closes the response,
        which cancels the request at the server."""
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        details: list[dict[str, Any]] = []
        extra_content: dict[str, Any] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}

        def partial() -> ChatResult:
            return ChatResult(content="".join(content), reasoning="".join(reasoning), tool_calls=[],
                              finish_reason=None, usage=usage, timings=timings,
                              elapsed_s=time.perf_counter() - started)

        async with self._client.stream("POST", "/chat/completions", json=body) as r:
            lines = r.aiter_lines()
            heard = False
            try:
                if r.status_code != 200:
                    async with asyncio.timeout(self.config.first_token_timeout_s):
                        text = (await r.aread()).decode("utf-8", "replace")
                    raise self._http_error(r.status_code, text)
                while True:
                    wait = self.config.liveness_timeout_s if heard else self.config.first_token_timeout_s
                    try:
                        async with asyncio.timeout(wait):
                            raw = await anext(lines)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        raise ModelStalled(self._stalled_message(heard, wait, check.tokens)) from None
                    heard = True
                    if not raw.startswith("data:"):
                        continue
                    line = raw[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ModelError(f"Malformed stream chunk: {exc}", body=self.redact(line)) from exc
                    if "error" in chunk:
                        raise self._stream_error(chunk, line)
                    usage = chunk.get("usage") or usage
                    timings = chunk.get("timings") or timings
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        stop = None
                        if delta:
                            check.chunk()
                        if text := _reasoning(delta):
                            reasoning.append(text)
                            if on_delta:
                                await on_delta("reasoning", text)
                            stop = check.feed("reasoning", text)
                        if text := delta.get("content"):
                            content.append(text)
                            if on_delta:
                                await on_delta("content", text)
                            stop = stop or check.feed("content", text)
                        if isinstance(delta.get("reasoning_details"), list):
                            _merge_details(details, delta["reasoning_details"])
                        if isinstance(delta.get("extra_content"), dict):
                            extra_content.update(delta["extra_content"])
                        for pos, tc in enumerate(delta.get("tool_calls") or []):
                            index = tc.get("index", pos)
                            if index not in calls:
                                calls[index] = {"id": "", "name": "", "args": [], "extra": {}}
                                stop = stop or check.tool_call(len(calls) - 1)
                            slot = calls[index]
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                args = _arguments_text(fn["arguments"])
                                slot["args"].append(args)
                                stop = stop or check.feed(f"tool_arguments:{index}", args)
                            slot["extra"].update({k: v for k, v in tc.items() if k not in _TOOL_CALL_KEYS})
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        if stop:
                            log.warning("%s: Jig stopped the reply as it streamed: %s", self.label,
                                        json.dumps(stop.record()))
                            raise ModelStopped(f"{self.label} stopped: {stop.describe()}", stop=stop,
                                               partial=partial())
            finally:
                await lines.aclose()
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

    def _stalled_message(self, heard: bool, wait: float, tokens: int) -> str:
        when = (f"for {wait:g} seconds in the middle of its reply, after about {tokens:,} tokens" if heard
                else f"for {wait:g} seconds after Jig asked it for a reply")
        setting = "liveness_timeout_s" if heard else "first_token_timeout_s"
        return (f"{self.label} server at {self.config.base_url} sent nothing {when}, so Jig stopped waiting: the "
                f"server looks stuck. Check that it is still running. If it is just slow, raise {setting} in the "
                "config.")

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
        return {"structured_output": True, "elapsed_s": round(result.elapsed_s, 2),
                "structured_retries": result.structured_retries}


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


def _error_message(text: str) -> str:
    """The message of an OpenAI-shaped error body ({"error": {"message": ...}}), or the start of the body."""
    try:
        error = json.loads(text).get("error")
    except (ValueError, AttributeError):
        return text[:200]
    message = error.get("message") if isinstance(error, dict) else error
    return str(message)[:200] if message else text[:200]


def _context_overflow(text: str, *, unwrap: bool = True) -> tuple[int, int] | None:
    """(prompt tokens, context size) from llama.cpp's exceed_context_size_error, which reports both. Ollama's
    OpenAI-compatible API passes its runner's error on as a JSON string in the message of its own error
    ({"error": {"message": "{\\"error\\": {...}}", "type": "invalid_request_error"}}), so that is read too."""
    try:
        error = json.loads(text).get("error")
    except (ValueError, AttributeError):
        return None
    if unwrap and isinstance(error, dict) and isinstance(error.get("message"), str):
        if inner := _context_overflow(error["message"], unwrap=False):
            return inner
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
