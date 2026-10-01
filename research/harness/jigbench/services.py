"""Local benchmark services.

The original programme ran a local attack web site and an exfiltration sink here. That self-authored
attack content has been removed: safety evaluation is being re-scoped onto *published* benchmarks (run in
process through a Jig adapter), and the memory experiment (C1) needs no local services. This module is kept
as a no-op so the runner's lifecycle is unchanged; a published-benchmark adapter can replace it later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ServiceError(RuntimeError):
    pass


def routes() -> dict[str, int]:
    """No benchmark hosts are routed in the current scope."""
    return {}


@dataclass
class BenchServices:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {"services": "none", "routes": routes()}
