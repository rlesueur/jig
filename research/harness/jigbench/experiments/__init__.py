"""Experiment definitions. Each module exposes `plan(cfg) -> list[trial]` and `async run_trial(ctx, trial)`.

The memory experiment (C1) and the safety experiment (D1) are implemented here. D1 runs the published
AgentDojo prompt-injection benchmark (MIT, arXiv:2406.13352) unchanged through a Jig adapter that executes
its existing cases against Jig's real defences. No attack content is authored in this programme; see
PROTOCOL.md.
"""

from . import c1_memory, d1_agentdojo

EXPERIMENTS = {
    "c1": c1_memory,
    "d1": d1_agentdojo,
}
