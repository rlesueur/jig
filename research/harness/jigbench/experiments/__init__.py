"""Experiment definitions. Each module exposes `plan(cfg) -> list[trial]` and `async run_trial(ctx, trial)`.

The memory experiment (C1) is implemented here. Safety evaluation (the former D1/D2/CD1) is being
re-scoped onto *published* agent prompt-injection benchmarks (for example AgentDojo and InjecAgent), run
through a Jig adapter that executes their existing test cases unchanged. No attack content is authored in
this programme; see PROTOCOL.md.
"""

from . import c1_memory

EXPERIMENTS = {
    "c1": c1_memory,
}
