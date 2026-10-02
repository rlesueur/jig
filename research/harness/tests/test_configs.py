"""Every shipped experiment config must name models the catalogue knows, so a run cannot fail at start-up
(d1_full.yaml named `granite-guardian-4.1-8b` instead of `granite-guardian41-8b` on 2 Oct 2026)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jigbench.provenance import model_catalogue

CONFIGS = sorted((Path(__file__).resolve().parents[1] / "configs").glob("*/*.yaml"))


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_config_models_are_catalogued(path: Path) -> None:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    catalogue = model_catalogue()
    endpoints = cfg["endpoints"]
    for name, ep in endpoints.items():
        if "server" in ep:
            assert ep["server"]["model"] in catalogue, f"{path.name}: endpoint {name} model not in models.toml"
    # A harness server is started with --alias <model key>, so whoever calls it must use that name.
    refs = [cfg.get("agent"), cfg.get("judge")] + [c.get("sentinel") for c in cfg["conditions"]]
    for ref in filter(None, refs):
        ep = endpoints[ref["endpoint"]]
        if "server" in ep:
            assert ref.get("name") == ep["server"]["model"], f"{path.name}: {ref} does not match the server alias"


def test_there_are_configs() -> None:
    assert len(CONFIGS) >= 4
