"""Core rules: hard-coded, non-overridable safety rules.

Custom rules and the Sentinel can only make things stricter than these, never
looser. A core ``block`` stops the action; a core ``ask`` forces a human
approval even if a custom rule says ``allow`` and the Sentinel agrees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..constants import Decision, ToolCategory
from ..errors import SecretNotFound
from ..tools.registry import ToolSpec
from ..tools.web import public_address_problem
from ..vault import Vault


@dataclass(frozen=True)
class CoreRule:
    id: str
    decision: Decision
    description: str


CORE_RULES: tuple[CoreRule, ...] = (
    CoreRule("research-read-only", Decision.BLOCK,
             "In research mode only read-only tools and private notes or memory writes may run."),
    CoreRule("no-credential-changes", Decision.BLOCK,
             "The agent may never create, change or reveal credentials or passwords; those steps stay with the human."),
    CoreRule("secret-allowlist", Decision.BLOCK,
             "A vault secret may only be used by the tools listed on that secret."),
    CoreRule("secret-outbound-needs-human", Decision.ASK,
             "Sending a vault secret to the internet always needs the user's approval."),
    CoreRule("no-local-network", Decision.BLOCK,
             "Outbound tools may only reach public internet addresses (never localhost, the model server, "
             "Jig's own API or the local network)."),
    CoreRule("human-only-actions", Decision.ASK,
             "Tools marked human-only (purchases, sending messages and similar) always need the user's approval."),
    CoreRule("sentinel-review", Decision.ASK,
             "Every outbound or side-effecting action is reviewed by the Sentinel; its deny cannot be overridden."),
)

_CORE_BY_ID = {r.id: r for r in CORE_RULES}
_CREDENTIAL_NAME = re.compile(r"password|passwd|credential|secret|vault|api[_-]?key|token", re.IGNORECASE)


@dataclass(frozen=True)
class CoreFinding:
    rule_id: str
    decision: Decision
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule_id, "decision": self.decision.value, "reason": self.reason}


def _finding(rule_id: str, reason: str) -> CoreFinding:
    return CoreFinding(rule_id, _CORE_BY_ID[rule_id].decision, reason)


def _urls(args: dict[str, Any]) -> list[str]:
    return [v for k, v in args.items() if isinstance(v, str) and (k in {"url", "uri", "endpoint"} or v.startswith(("http://", "https://")))]


async def evaluate_core(spec: ToolSpec, args: dict[str, Any], vault: Vault) -> list[CoreFinding]:
    findings: list[CoreFinding] = []
    if spec.category == ToolCategory.CREDENTIALS or _CREDENTIAL_NAME.search(spec.name):
        findings.append(_finding("no-credential-changes", f"tool {spec.name!r} touches credentials"))

    refs = Vault.references(args)
    for name in sorted(refs):
        try:
            allowed = vault.describe(name)["allowed_tools"]
        except SecretNotFound:
            findings.append(_finding("secret-allowlist", f"secret {name!r} does not exist"))
            continue
        if spec.name not in allowed:
            findings.append(_finding("secret-allowlist", f"secret {name!r} is not allowed for tool {spec.name!r}"))
    if refs and spec.outbound:
        findings.append(_finding("secret-outbound-needs-human", f"secrets {sorted(refs)} would leave the machine"))

    if spec.outbound:
        for url in _urls(args):
            if problem := await public_address_problem(url):
                findings.append(_finding("no-local-network", problem))

    if spec.human_only:
        findings.append(_finding("human-only-actions", f"{spec.name!r} always needs the user's approval"))
    return findings
