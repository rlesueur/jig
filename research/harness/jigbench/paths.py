"""Well-known locations inside research/."""

from __future__ import annotations

from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[2]
REPO = RESEARCH.parent
HARNESS = RESEARCH / "harness"
CONFIGS = HARNESS / "configs"
RESULTS = RESEARCH / "results"
LOGS = RESEARCH / "logs"
DATA = RESEARCH / "data"
MODELS_TOML = RESEARCH / "models" / "models.toml"
MODELS_LOCK = RESEARCH / "models" / "models.lock.json"
PAPER_GENERATED = RESEARCH / "paper" / "generated"
SUBMISSIONS = RESULTS / "submissions"
