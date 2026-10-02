"""Tool registry. Tools are async functions declared with ``@registry.tool``.

The JSON schema is derived from the function signature; descriptions come
from the decorator. Every tool declares its ``effect`` and whether it is
``outbound``; the policy gate enforces modes and review from these tags.
"""

from __future__ import annotations

import inspect
import re
import types
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..constants import CATEGORY_TO_VARIANT, RESEARCH_ALLOWED_EFFECTS, Decision, Effect, Mode, TaskVariant, ToolCategory
from ..errors import ToolArgumentError, ToolNotFound

_JSON_TYPES: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean",
                               dict: "object", list: "array"}


@dataclass
class ToolContext:
    """What a tool may touch. Deliberately excludes the vault and the model."""

    sandbox: Any
    memory: Any
    store: Any
    config: Any
    http: Any
    mode: Mode
    run_id: str
    task_id: str | None
    # Image understanding (jig.vision.VisionService); raises VisionUnavailable when vision is off.
    vision: Any = None
    # The container sandbox (jig.sandbox_container.ContainerSandbox) when that backend is selected.
    container: Any = None
    # Connected accounts (jig.connectors.Connectors). It sends authorised requests for a tool and never
    # hands a token to the tool or the model.
    connectors: Any = None


ToolFn = Callable[..., Awaitable[Any]]
# Looks up, read-only, what a call refers to (the thread a reply goes to, the event it changes), so the
# Sentinel and the approval card can show it. Returns a short JSON-able dict.
ResolveFn = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]
# Checks fixed limits from the config (a recipient allow-list, a required subject prefix) before review.
# Returns why the call is refused, or None.
PrecheckFn = Callable[[Any, dict[str, Any], dict[str, Any] | None], str | None]


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: ToolFn
    parameters: dict[str, Any]
    effect: Effect
    category: ToolCategory
    outbound: bool = False
    variant: TaskVariant | None = None
    # Tools whose every call must be confirmed by a human (purchases, sending
    # messages). Enforced by a core rule, so custom rules cannot relax it.
    human_only: bool = False
    # Decision used when no custom rule matches.
    default_decision: Decision = Decision.ALLOW
    # When set, the tool is offered to the model only while this returns True (a connected account).
    # The gate still finds it, and the tool fails clearly, if the model calls it anyway.
    available: Callable[[], bool] | None = None
    resolve: ResolveFn | None = None
    precheck: PrecheckFn | None = None
    # Offered only in background task runs (reading an earlier task's result), never in chat.
    tasks_only: bool = False

    def is_available(self) -> bool:
        return self.available is None or bool(self.available())

    @property
    def read_only(self) -> bool:
        return self.effect == Effect.READ

    @property
    def avatar_variant(self) -> TaskVariant:
        return self.variant or CATEGORY_TO_VARIANT[self.category]

    def allowed_in(self, mode: Mode) -> bool:
        return mode == Mode.ACTION or self.effect in RESEARCH_ALLOWED_EFFECTS

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "effect": self.effect.value,
            "read_only": self.read_only,
            "outbound": self.outbound,
            "category": self.category.value,
            "avatar_variant": self.avatar_variant.value,
            "human_only": self.human_only,
            "default_decision": self.default_decision.value,
            "available": self.is_available(),
        }

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check arguments in place. ``null`` for an optional argument means "use the default"."""
        props = self.parameters["properties"]
        required = set(self.parameters.get("required", []))
        for key in [k for k, v in args.items() if v is None and k in props and k not in required]:
            del args[key]
        unknown = set(args) - set(props)
        if unknown:
            raise ToolArgumentError(f"{self.name}: unknown arguments {sorted(unknown)}")
        missing = [k for k in self.parameters.get("required", []) if k not in args]
        if missing:
            raise ToolArgumentError(f"{self.name}: missing required arguments {missing}")
        for key, value in args.items():
            expected = props[key].get("type")
            if expected and not _matches(value, expected):
                raise ToolArgumentError(f"{self.name}: argument {key!r} must be of type {expected}")
            if "enum" in props[key] and value not in props[key]["enum"]:
                raise ToolArgumentError(f"{self.name}: argument {key!r} must be one of {props[key]['enum']}")
            if "pattern" in props[key] and not re.fullmatch(props[key]["pattern"], value):
                raise ToolArgumentError(f"{self.name}: argument {key!r} must match {props[key]['pattern']}")
        return args


def _matches(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = {"string": str, "boolean": bool, "object": dict, "array": list}[expected]
    return isinstance(value, py)


def _json_type(annotation: Any) -> dict[str, Any]:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(inner) != 1:
            raise TypeError(f"unsupported union annotation {annotation!r}")
        return _json_type(inner[0])
    if origin is typing.Literal:
        values = list(typing.get_args(annotation))
        return {"type": _JSON_TYPES[type(values[0])], "enum": values}
    if origin in (list, dict):
        return {"type": _JSON_TYPES[origin]}
    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    raise TypeError(f"unsupported tool parameter annotation {annotation!r}")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def tool(
        self,
        *,
        description: str,
        effect: Effect,
        category: ToolCategory,
        args: dict[str, str] | None = None,
        name: str | None = None,
        outbound: bool = False,
        variant: TaskVariant | None = None,
        human_only: bool = False,
        default_decision: Decision = Decision.ALLOW,
        available: Callable[[], bool] | None = None,
        resolve: ResolveFn | None = None,
        precheck: PrecheckFn | None = None,
        tasks_only: bool = False,
    ) -> Callable[[ToolFn], ToolFn]:
        def decorator(fn: ToolFn) -> ToolFn:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"tool {fn.__name__} must be async")
            tool_name = name or fn.__name__
            if tool_name in self._tools:
                raise ValueError(f"tool {tool_name!r} is already registered")
            hints = typing.get_type_hints(fn)
            params = list(inspect.signature(fn).parameters.values())
            if not params or params[0].name != "ctx":
                raise TypeError(f"tool {tool_name} must take 'ctx' as its first parameter")
            props: dict[str, Any] = {}
            required: list[str] = []
            docs = args or {}
            for p in params[1:]:
                prop = _json_type(hints[p.name])
                if p.name in docs:
                    prop["description"] = docs[p.name]
                if p.default is inspect.Parameter.empty:
                    required.append(p.name)
                else:
                    prop["default"] = p.default
                props[p.name] = prop
            undocumented = set(docs) - set(props)
            if undocumented:
                raise ValueError(f"tool {tool_name}: docs for unknown args {sorted(undocumented)}")
            self._tools[tool_name] = ToolSpec(
                name=tool_name,
                description=description,
                fn=fn,
                parameters={"type": "object", "properties": props, "required": required,
                            "additionalProperties": False},
                effect=effect,
                category=category,
                outbound=outbound,
                variant=variant,
                human_only=human_only,
                default_decision=default_decision,
                available=available,
                resolve=resolve,
                precheck=precheck,
                tasks_only=tasks_only,
            )
            return fn

        return decorator

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(f"no tool named {name!r}") from None

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def available(self) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.is_available()]

    def for_mode(self, mode: Mode, *, task: bool = True) -> list[ToolSpec]:
        return [t for t in self.available() if t.allowed_in(mode) and (task or not t.tasks_only)]

    def schemas_for_mode(self, mode: Mode, *, task: bool = True) -> list[dict[str, Any]]:
        return [t.schema() for t in self.for_mode(mode, task=task)]
