"""Experiment definitions. Each module exposes `plan(cfg) -> list[trial]` and `async run_trial(ctx, trial)`."""

from . import c1_memory, cd1_poisoning, d1_sentinel, d2_injection

EXPERIMENTS = {
    "c1": c1_memory,
    "d1": d1_sentinel,
    "d2": d2_injection,
    "cd1": cd1_poisoning,
}
