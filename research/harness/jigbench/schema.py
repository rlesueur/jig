"""Results schema (version 1). Checked by `jigbench submit-results`; documented in CONTRIBUTING-RESULTS.md."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1

TRIAL_REQUIRED = {"trial_id": str, "condition": str, "seed": int, "status_harness": str, "started_at": str,
                  "duration_s": (int, float), "endpoint_models": dict}
EXPERIMENT_REQUIRED: dict[str, dict[str, Any]] = {
    "d1": {"label": str, "tool": str, "decision": str, "caught": bool, "correct": bool},
    "d2": {"payload": str, "channel": str, "attempted": bool, "leaked": bool, "utility": bool, "approvals": int},
    "cd1": {"payload": str, "poison_written": bool, "poison_retrieved": bool, "attack_success": bool,
            "leaked": bool},
    "c1": {"question_id": str, "strategy": str, "correct": bool, "total_prompt_tokens": int},
}
SESSION_REQUIRED = {"git": dict, "hardware": dict, "endpoints": dict, "config": dict}


class SchemaError(ValueError):
    pass


def _check(obj: dict[str, Any], spec: dict[str, Any], where: str) -> list[str]:
    errs = []
    for key, typ in spec.items():
        if key not in obj:
            errs.append(f"{where}: missing {key!r}")
        elif not isinstance(obj[key], typ) or (typ is int and isinstance(obj[key], bool)):
            errs.append(f"{where}: {key!r} has type {type(obj[key]).__name__}")
    return errs


def validate_run(meta: dict[str, Any], trials: list[dict[str, Any]]) -> None:
    exp = meta.get("experiment")
    errs = [] if exp in EXPERIMENT_REQUIRED else [f"run.json: unknown experiment {exp!r}"]
    if not meta.get("sessions"):
        errs.append("run.json: no provenance sessions")
    for i, s in enumerate(meta.get("sessions") or []):
        errs += _check(s, SESSION_REQUIRED, f"run.json session {i}")
        if not s.get("endpoints"):
            errs.append(f"run.json session {i}: no endpoint provenance (model, quantisation, server build)")
    for t in trials:
        where = f"trial {t.get('trial_id', '?')}"
        errs += _check(t, TRIAL_REQUIRED, where)
        if t.get("status_harness") == "ok" and exp in EXPERIMENT_REQUIRED:
            errs += _check(t, EXPERIMENT_REQUIRED[exp], where)
    if not trials:
        errs.append("trials.jsonl is empty")
    if errs:
        raise SchemaError(f"{len(errs)} schema problem(s):\n  " + "\n  ".join(errs[:40]))
