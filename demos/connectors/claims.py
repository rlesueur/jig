"""Does Jig's reply claim an action that didn't happen? The rule is data, in demos/lib/claims.json (its "rule" says
it in words), shared with demos/lib/claims.mjs so the connector runs and the launch demos judge replies alike."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CLAIMS: dict[str, Any] = json.loads((Path(__file__).resolve().parent.parent / "lib" / "claims.json")
                                    .read_text(encoding="utf-8"))


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def clauses(text: str) -> list[str]:
    return [c.strip() for c in _re(CLAIMS["split"]).split(text or "") if c and c.strip()]


def _claim_in(clause: str, kind: dict[str, Any]) -> dict[str, Any] | None:
    bare = bool(_re(CLAIMS["bare"]).search(clause))
    if kind.get("states") and (m := _re(kind["states"]).search(clause)):
        return {"verb": m.group(0), "form": "state", "bare": bare}
    for v in kind["verbs"]:
        if v.get("about") and not _re(v["about"]).search(clause):
            continue
        verb = f"({v['verbs']})\\b(?!{CLAIMS['someone_else']})"
        forms = [("first person", CLAIMS["first_person"] + verb)]
        if re.search(r"\b(i|we)\b", clause, re.IGNORECASE):
            forms.append(("first person", rf"\band\s+((then|also|have)\s+)?{verb}"))
        forms.append(("passive", f"({CLAIMS['perfect_passive']}){verb}"))
        if kind.get("plain_passive"):
            forms.append(("passive", f"({CLAIMS['plain_passive']}){verb}"))
            forms.append(("headline", f"{CLAIMS['headline']}{verb}(?={CLAIMS['headline_after']})"))
        forms.append(("clause start", CLAIMS["lead"] + verb))
        for form, pattern in forms:
            if m := _re(pattern).search(clause):
                return {"verb": m.group(0).strip(), "form": form, "bare": bare}
    return None


def claims_of(text: str, kind: str) -> list[dict[str, Any]]:
    """The clauses of text that claim an action of this kind ("save", "send", "book", "create", "order")."""
    k = CLAIMS["kinds"][kind]
    out = []
    for clause in clauses(text):
        if _re(CLAIMS["negation"]).search(clause) or _re(CLAIMS["condition"]).search(clause):
            continue
        if found := _claim_in(clause, k):
            out.append({"clause": clause, **found})
    return out


def claimed(text: str, kind: str, about: str | None = None, unless: str | None = None) -> list[str]:
    """The claims of this kind, in words for the check's detail: those in clauses that mention `about` (a
    pattern) and not `unless`, and those that are only the verb."""
    return [f"\"{c['clause'][:160]}\" ({c['form']}: {c['verb']})" for c in claims_of(text, kind)
            if c["bare"] or ((not about or re.search(about, c["clause"], re.IGNORECASE))
                             and not (unless and re.search(unless, c["clause"], re.IGNORECASE)))]
