"""Checks a model's structured answer against the JSON schema Jig asked for.

Servers do not all enforce the schema (Anthropic ignores ``strict``; a tool call's arguments are rarely
constrained), so Jig checks every structured answer itself. Only the keywords Jig's own schemas use are
supported, and a schema with any other keyword is refused rather than partly checked.

Problems name the place in the answer and the rule it broke, never the value found there.
"""

from __future__ import annotations

from typing import Any

_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "enum", "items", "minItems",
                       "maxItems", "description", "title"})
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def check_schema(schema: dict[str, Any], path: str = "$") -> None:
    """Raise ``ValueError`` if the schema uses a keyword or type this module cannot check."""
    if unknown := sorted(set(schema) - _KEYWORDS):
        raise ValueError(f"schema at {path} uses keywords Jig cannot check: {unknown}")
    types = schema.get("type", [])
    for t in [types] if isinstance(types, str) else types:
        if t not in _TYPES:
            raise ValueError(f"schema at {path} has an unknown type {t!r}")
    if not isinstance(schema.get("additionalProperties", False), bool):
        raise ValueError(f"schema at {path}: additionalProperties must be true or false")
    for name, sub in (schema.get("properties") or {}).items():
        check_schema(sub, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        check_schema(schema["items"], f"{path}[]")


def problems(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Every way ``value`` breaks ``schema`` (empty if it matches)."""
    found: list[str] = []
    types = schema.get("type")
    if types is not None:
        allowed = [types] if isinstance(types, str) else list(types)
        if not any(_TYPES[t](value) for t in allowed):
            return [f"{path} must be {' or '.join(allowed)}"]
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{path} must be one of {', '.join(repr(v) for v in schema['enum'])}")
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        found += [f"{path}.{name} is missing" for name in schema.get("required", []) if name not in value]
        if schema.get("additionalProperties") is False and (extra := sorted(set(value) - set(properties))):
            found.append(f"{path} has properties the schema does not allow: {', '.join(extra)}")
        for name, sub in properties.items():
            if name in value:
                found += problems(value[name], sub, f"{path}.{name}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            found.append(f"{path} must have at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            found.append(f"{path} must have at most {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                found += problems(item, schema["items"], f"{path}[{i}]")
    return found
