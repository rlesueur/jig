"""Package a finished run for contribution to the living benchmark (see CONTRIBUTING-RESULTS.md)."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .paths import SUBMISSIONS
from .report import load, report
from .schema import SCHEMA_VERSION, validate_run


def submit(run_dir: Path, *, contributor: str, notes: str = "") -> str:
    run_dir = run_dir.resolve()
    meta, ok, errors = load(run_dir)
    validate_run(meta, ok + errors)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", contributor):
        raise ValueError("contributor must be a short handle (letters, digits, '_', '.', '-')")
    report([run_dir])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = SUBMISSIONS / f"{stamp}-{contributor}-{meta['experiment']}-{meta['name']}"
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    dest.mkdir(parents=True)
    raw = (run_dir / "trials.jsonl").read_bytes()
    with gzip.open(dest / "trials.jsonl.gz", "wb") as fh:
        fh.write(raw)
    for name in ("run.json", "summary.md", "summary.json"):
        shutil.copy2(run_dir / name, dest / name)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "contributor": contributor,
        "experiment": meta["experiment"],
        "run": meta["name"],
        "trials_ok": len(ok),
        "trials_error": len(errors),
        "trials_sha256": hashlib.sha256(raw).hexdigest(),
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notes": notes,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return (f"Packaged {len(ok)} trials ({len(errors)} harness errors) into {dest}\n"
            "Next: commit that folder on a branch and open a pull request (see research/CONTRIBUTING-RESULTS.md).")
