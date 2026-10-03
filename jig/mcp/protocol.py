"""What an MCP tool declares, and the JSON schema Jig will check its arguments against.

A tool declares itself in ``_meta.jig`` (effect, outbound, category). The same three fields are also
read from ``annotations`` when a server puts them there. MCP's own hints count as a declaration of the
effect only: ``readOnlyHint`` true is read, and ``destructiveHint`` true or ``readOnlyHint`` false is a
side effect. ``openWorldHint`` counts as a declaration of outbound.

A tool that declares no effect fails closed: it is treated as able to send, change or delete, it is
outbound so the Sentinel reviews it, and it always needs approval. A declared read with no outbound
declaration is still reviewed by the Sentinel, because Jig does not assume a read stays on this computer.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..constants import Decision, Effect, ToolCategory

# Protocol versions this client will speak. The server picks one of them in its initialize result.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
CLIENT_PROTOCOL = PROTOCOL_VERSIONS[0]

_EFFECTS = {e.value: e for e in Effect}
_CATEGORIES = {c.value: c for c in ToolCategory}
_NAME = re.compile(r"[^A-Za-z0-9_]+")


def tool_name(server_id: str, remote: str) -> str:
    """A function name the model can call. The remote name is kept intact in the description."""
    stem = f"mcp_{server_id.replace('-', '_')}_{_NAME.sub('_', remote)}"
    stem = re.sub(r"_+", "_", stem).strip("_")
    if len(stem) <= 64:
        return stem
    digest = hashlib.sha256(f"{server_id}\n{remote}".encode()).hexdigest()[:8]
    return stem[:55].rstrip("_") + "_" + digest


def parameters(schema: Any) -> dict[str, Any]:
    """The tool's input schema, narrowed to an object Jig can check. Anything else accepts no arguments."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    props = schema.get("properties")
    if not isinstance(props, dict):
        props = {}
    required = [name for name in schema.get("required") or [] if isinstance(name, str) and name in props]
    extra = schema.get("additionalProperties")
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": extra if isinstance(extra, bool) else False}


def classify(tool: dict[str, Any]) -> dict[str, Any]:
    """Effect, outbound, category, and whether the tool declared an effect."""
    meta = tool.get("_meta")
    jig = meta.get("jig") if isinstance(meta, dict) and isinstance(meta.get("jig"), dict) else {}
    ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}

    effect_name = _text(jig.get("effect")) or _text(ann.get("effect"))
    effect = _EFFECTS.get(effect_name) if effect_name else None
    declared = effect is not None
    if effect is None and ann.get("readOnlyHint") is True and ann.get("destructiveHint") is not True:
        effect, declared = Effect.READ, True
    elif effect is None and (ann.get("destructiveHint") is True or ann.get("readOnlyHint") is False):
        effect, declared = Effect.SIDE_EFFECT, True
    if effect is None:
        effect = Effect.SIDE_EFFECT

    if isinstance(jig.get("outbound"), bool):
        outbound = jig["outbound"]
    elif isinstance(ann.get("outbound"), bool):
        outbound = ann["outbound"]
    elif isinstance(ann.get("openWorldHint"), bool):
        outbound = ann["openWorldHint"]
    else:
        # Not declared. A side effect is already reviewed; a read is reviewed too, rather than assumed local.
        outbound = True

    category_name = _text(jig.get("category")) or _text(ann.get("category"))
    category = _CATEGORIES.get(category_name) if category_name else None
    category_declared = category is not None
    if category is None:
        category = ToolCategory.FILES

    return {"effect": effect, "outbound": outbound, "category": category, "declared": declared,
            "category_declared": category_declared,
            "decision": Decision.ALLOW if declared and effect is Effect.READ else Decision.ASK}


def describe(label: str, remote: str, text: str, info: dict[str, Any]) -> str:
    """What the model is told. The server's own words first, then how Jig will treat the tool."""
    own = " ".join((text or "This tool has no description.").split())
    if len(own) > 500:
        own = own[:500].rstrip() + "…"
    if info["declared"]:
        treatment = (f"Declared effect {info['effect'].value}, outbound {str(info['outbound']).lower()}, "
                     f"category {info['category'].value}.")
        if not info["category_declared"]:
            treatment += " It did not declare a category Jig knows, so it is shown as files."
        if info["outbound"] and info["effect"] is Effect.READ:
            treatment += " The safety checker reviews it before it runs."
    else:
        treatment = ("This tool did not declare an effect, so Jig treats it as able to send, change or delete. "
                     "It always asks you first, and the safety checker reviews it.")
    return f"{own} MCP server {label}, tool {remote}. {treatment}"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
