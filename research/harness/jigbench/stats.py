"""Small, dependency-light statistics used by the report."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    """Proportion with a Wilson score 95% interval: (p, lo, hi). n == 0 gives NaNs."""
    if n == 0:
        return math.nan, math.nan, math.nan
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_mean(xs: Sequence[float], iters: int = 5000, seed: int = 0) -> tuple[float, float, float]:
    """Mean with a percentile bootstrap 95% interval."""
    xs = [float(x) for x in xs]
    if not xs:
        return math.nan, math.nan, math.nan
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(iters))
    return sum(xs) / n, means[int(0.025 * iters)], means[int(0.975 * iters) - 1]


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (key, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[key] = running
    return out


def fisher_exact(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided Fisher exact test for two proportions."""
    from scipy.stats import fisher_exact as fe

    return float(fe([[k1, n1 - k1], [k2, n2 - k2]]).pvalue)


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar test from the discordant pair counts b and c."""
    from scipy.stats import binomtest

    n = b + c
    return 1.0 if n == 0 else float(binomtest(b, n, 0.5).pvalue)


def fmt_ci(p: float, lo: float, hi: float, pct: bool = True) -> str:
    if math.isnan(p):
        return "--"
    if pct:
        return f"{100 * p:.0f}% [{100 * lo:.0f}, {100 * hi:.0f}]"
    return f"{p:.2f} [{lo:.2f}, {hi:.2f}]"
