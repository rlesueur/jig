"""Adapter that runs AgentDojo's published prompt-injection benchmark (MIT licence, arXiv 2406.13352)
against Jig's real defences, without changing any AgentDojo case.

AgentDojo ships task suites (banking, slack, travel, workspace), benign user tasks, injection tasks and a
set of attacks that place the injection text into tool outputs. We keep all of that, and the scoring, exactly
as published. What this module adds is the bridge so that every tool call the agent makes passes through
*Jig's own gate* (`jig.policy.gate.ToolExecutor`): mode (read-only vs action), core rules, the user's custom
rules, the Sentinel reviewer, and the approval queue. Nothing here authors, extends or modifies any attack:
the attack strings come only from AgentDojo.

The bridge:
  * every AgentDojo tool is wrapped as a real Jig `ToolSpec` whose effect/outbound/category come from the
    explicit table below (no silent fallback: an unclassified tool raises), and whose implementation calls
    AgentDojo's own `runtime.run_function(env, ...)` so the AgentDojo environment is what actually mutates;
  * `JigGatedToolsExecutor` replaces AgentDojo's `ToolsExecutor` in the pipeline. For each tool call it asks
    Jig's gate to execute the call; a gate block (mode violation, rule, Sentinel deny, denied approval)
    becomes a tool error fed back to the model, exactly as a real Jig run would behave.

The Jig gate is async and lives on the harness event loop (so the approval queue works); AgentDojo's pipeline
is synchronous and runs in a worker thread. Calls are marshalled back with `run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatMessage, ChatToolResultMessage, text_content_block_from_string

from jig.constants import Effect, Mode, ToolCategory
from jig.errors import ToolError
from jig.model import ToolCall
from jig.policy.gate import CallContext, ToolExecutor
from jig.tools.registry import ToolRegistry, ToolSpec

# --- Explicit classification of every AgentDojo v1 tool into Jig's taxonomy -------------------------------
# (effect, outbound, category). READ = information only; PRIVATE_WRITE = changes the user's own local state
# (allowed in research mode, not reviewed unless outbound); SIDE_EFFECT = acts on the outside world or is
# irreversible (refused in research mode, always Sentinel-reviewed). `outbound` marks a call that sends data
# out or reads untrusted external content (web), which the Sentinel reviews even when it only reads.
_READ, _PW, _SE = Effect.READ, Effect.PRIVATE_WRITE, Effect.SIDE_EFFECT
_WEB, _FILES, _NOTES, _TIME, _COMMERCE, _CRED = (
    ToolCategory.WEB, ToolCategory.FILES, ToolCategory.NOTES, ToolCategory.TIME,
    ToolCategory.COMMERCE, ToolCategory.CREDENTIALS)

TOOL_CLASS: dict[str, tuple[Effect, bool, ToolCategory]] = {
    # workspace - calendar
    "get_current_day": (_READ, False, _TIME),
    "get_day_calendar_events": (_READ, False, _TIME),
    "search_calendar_events": (_READ, False, _TIME),
    "create_calendar_event": (_PW, False, _TIME),
    "cancel_calendar_event": (_PW, False, _TIME),
    "reschedule_calendar_event": (_PW, False, _TIME),
    "add_calendar_event_participants": (_SE, True, _TIME),
    # workspace - email
    "get_received_emails": (_READ, False, _NOTES),
    "get_sent_emails": (_READ, False, _NOTES),
    "get_draft_emails": (_READ, False, _NOTES),
    "get_unread_emails": (_READ, False, _NOTES),
    "search_emails": (_READ, False, _NOTES),
    "search_contacts_by_name": (_READ, False, _NOTES),
    "search_contacts_by_email": (_READ, False, _NOTES),
    "delete_email": (_PW, False, _NOTES),
    "send_email": (_SE, True, _NOTES),
    # workspace - files
    "list_files": (_READ, False, _FILES),
    "get_file_by_id": (_READ, False, _FILES),
    "search_files": (_READ, False, _FILES),
    "search_files_by_filename": (_READ, False, _FILES),
    "create_file": (_PW, False, _FILES),
    "append_to_file": (_PW, False, _FILES),
    "delete_file": (_PW, False, _FILES),
    "share_file": (_SE, True, _FILES),
    # travel - reads
    "get_user_information": (_READ, False, _NOTES),
    "get_all_hotels_in_city": (_READ, False, _COMMERCE),
    "get_hotels_prices": (_READ, False, _COMMERCE),
    "get_hotels_address": (_READ, False, _COMMERCE),
    "get_rating_reviews_for_hotels": (_READ, False, _COMMERCE),
    "get_all_restaurants_in_city": (_READ, False, _COMMERCE),
    "get_restaurants_address": (_READ, False, _COMMERCE),
    "get_rating_reviews_for_restaurants": (_READ, False, _COMMERCE),
    "get_cuisine_type_for_restaurants": (_READ, False, _COMMERCE),
    "get_dietary_restrictions_for_all_restaurants": (_READ, False, _COMMERCE),
    "get_contact_information_for_restaurants": (_READ, False, _COMMERCE),
    "get_price_for_restaurants": (_READ, False, _COMMERCE),
    "check_restaurant_opening_hours": (_READ, False, _COMMERCE),
    "get_all_car_rental_companies_in_city": (_READ, False, _COMMERCE),
    "get_car_types_available": (_READ, False, _COMMERCE),
    "get_car_fuel_options": (_READ, False, _COMMERCE),
    "get_car_rental_address": (_READ, False, _COMMERCE),
    "get_car_price_per_day": (_READ, False, _COMMERCE),
    "get_rating_reviews_for_car_rental": (_READ, False, _COMMERCE),
    "get_flight_information": (_READ, False, _COMMERCE),
    # travel - actions
    "reserve_hotel": (_SE, True, _COMMERCE),
    "reserve_restaurant": (_SE, True, _COMMERCE),
    "reserve_car_rental": (_SE, True, _COMMERCE),
    # banking - reads
    "get_balance": (_READ, False, _COMMERCE),
    "get_iban": (_READ, False, _CRED),
    "get_most_recent_transactions": (_READ, False, _COMMERCE),
    "get_scheduled_transactions": (_READ, False, _COMMERCE),
    "get_user_info": (_READ, False, _NOTES),
    "read_file": (_READ, False, _FILES),
    # banking - actions
    "send_money": (_SE, True, _COMMERCE),
    "schedule_transaction": (_SE, True, _COMMERCE),
    "update_scheduled_transaction": (_SE, True, _COMMERCE),
    "update_user_info": (_SE, False, _CRED),
    "update_password": (_SE, False, _CRED),
    # slack - reads
    "get_channels": (_READ, False, _NOTES),
    "get_users_in_channel": (_READ, False, _NOTES),
    "read_channel_messages": (_READ, False, _NOTES),
    "read_inbox": (_READ, False, _NOTES),
    "get_webpage": (_READ, True, _WEB),
    # slack - actions
    "send_channel_message": (_SE, True, _NOTES),
    "send_direct_message": (_SE, True, _NOTES),
    "post_webpage": (_SE, True, _WEB),
    "add_user_to_channel": (_SE, False, _NOTES),
    "invite_user_to_slack": (_SE, True, _NOTES),
    "remove_user_from_slack": (_SE, True, _NOTES),
}


def classify(name: str) -> tuple[Effect, bool, ToolCategory]:
    """Return (effect, outbound, category) for an AgentDojo tool, raising if it is not in the table."""
    try:
        return TOOL_CLASS[name]
    except KeyError:
        raise KeyError(
            f"AgentDojo tool {name!r} is not classified in TOOL_CLASS; refusing to guess its effect "
            "(this would silently change which defences apply). Add it to the table.") from None


@dataclass
class EnvHolder:
    """Shared, mutable handle to the AgentDojo runtime and environment for the current query."""

    runtime: FunctionsRuntime | None = None
    env: Env = field(default_factory=EmptyEnv)


def _schema_for(func: Any) -> dict[str, Any]:
    """A permissive Jig argument schema (names + required) derived from AgentDojo's pydantic parameter model.
    Type checking is left to AgentDojo's own validation in run_function; Jig only needs the argument names."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for fname, fld in func.parameters.model_fields.items():
        props[fname] = {}
        if fld.is_required():
            required.append(fname)
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def build_registry(tools: Sequence[Any], holder: EnvHolder) -> ToolRegistry:
    """Wrap each AgentDojo tool as a real Jig ToolSpec. The spec's implementation runs the AgentDojo tool
    against whatever env the holder currently points at, so the real AgentDojo environment mutates."""
    registry = ToolRegistry()
    for func in tools:
        effect, outbound, category = classify(func.name)

        def make_fn(tool_name: str):
            async def fn(ctx: Any, **kwargs: Any) -> Any:
                if holder.runtime is None:
                    raise ToolError("no AgentDojo runtime bound for this call")
                result, error = holder.runtime.run_function(holder.env, tool_name, kwargs)
                if error:
                    raise ToolError(error)
                return result
            return fn

        registry._tools[func.name] = ToolSpec(
            name=func.name,
            description=func.description,
            fn=make_fn(func.name),
            parameters=_schema_for(func),
            effect=effect,
            category=category,
            outbound=outbound,
        )
    return registry


def gated_executor(jig: Any, registry: ToolRegistry) -> ToolExecutor:
    """A Jig ToolExecutor using the AgentDojo-tool registry but all of this Jig instance's real policy
    machinery: the configured rules, Sentinel, approval queue, vault, audit and event bus."""
    return ToolExecutor(registry=registry, rules=jig.rules, sentinel=jig.sentinel, approvals=jig.approvals,
                        vault=jig.vault, audit=jig.audit, bus=jig.bus, context_factory=jig._tool_context)


GATE_BLOCKS = frozenset({"ModeViolation", "PolicyBlocked", "ApprovalDenied"})


class ReviewerFailed(RuntimeError):
    """The Sentinel/guard reviewer itself failed. Jig fails closed on this, but a crashed reviewer is not a
    measured defence decision, so the trial is a harness error (retried later), never a scored block."""


class JigGatedToolsExecutor(BasePipelineElement):
    """AgentDojo pipeline element: run each requested tool call through Jig's gate instead of straight
    execution. A gate block is reported to the model as a tool error (what a real Jig run would show).

    `blocked` counts only gate refusals (mode, core/custom rule, Sentinel deny, denied approval);
    `tool_errors` counts calls the gate allowed that then failed in the tool (e.g. bad arguments)."""

    def __init__(self, executor: ToolExecutor, holder: EnvHolder, loop: asyncio.AbstractEventLoop,
                 mode: Mode, run_id: str, intent: str) -> None:
        self.executor = executor
        self.holder = holder
        self.loop = loop
        self.mode = mode
        self.run_id = run_id
        self.intent = intent
        self.blocked = 0
        self.executed = 0
        self.tool_errors = 0
        self.blocks_by_type: dict[str, int] = {}

    def _run_call(self, name: str, args: dict[str, Any], call_id: str) -> tuple[str, str | None]:
        import json
        call = ToolCall(id=call_id or "call", name=name, arguments_raw=json.dumps(args))
        ctx = CallContext(run_id=self.run_id, task_id=None, mode=self.mode, intent=self.intent)
        outcome = asyncio.run_coroutine_threadsafe(self.executor.execute(call, ctx), self.loop).result()
        if outcome.ok:
            self.executed += 1
        elif outcome.error_type == "SentinelError":
            raise ReviewerFailed(f"reviewer failed on {name!r}: {outcome.error}")
        elif outcome.error_type in GATE_BLOCKS:
            self.blocked += 1
            self.blocks_by_type[outcome.error_type] = self.blocks_by_type.get(outcome.error_type, 0) + 1
        else:
            self.tool_errors += 1
        return outcome.message_content(), (None if outcome.ok else outcome.error)

    def query(self, query: str, runtime: FunctionsRuntime, env: Env = EmptyEnv(),
              messages: Sequence[ChatMessage] = [], extra_args: dict = {}
              ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        self.holder.runtime = runtime
        self.holder.env = env
        if not messages or messages[-1]["role"] != "assistant":
            return query, runtime, env, messages, extra_args
        tool_calls = messages[-1]["tool_calls"]
        if not tool_calls:
            return query, runtime, env, messages, extra_args
        results: list[ChatMessage] = []
        for tc in tool_calls:
            content, error = self._run_call(tc.function, dict(tc.args), tc.id or "")
            results.append(ChatToolResultMessage(
                role="tool", content=[text_content_block_from_string(content)],
                tool_call_id=tc.id, tool_call=tc, error=error))
        return query, runtime, env, [*messages, *results], extra_args
