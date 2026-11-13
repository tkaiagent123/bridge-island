"""Percentile bootstrap intervals, seeded, with no dependency beyond the standard library."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

T = TypeVar("T")
Stat = Callable[[Sequence[T]], float | None]
BOOTSTRAPS = 1000


def bootstrap(
    rows: Sequence[T], stats: Mapping[str, Stat[T]], seed: int, b: int = BOOTSTRAPS
) -> dict[str, tuple[float | None, float | None, float | None]]:
    """For each statistic: ``(value, low, high)``, the 95% interval from resampling the rows.

    The same resamples serve every statistic. A statistic that is undefined for the rows (a ratio
    with nothing in the denominator) is ``None``; an interval needs at least 20 defined resamples.
    """
    rng = random.Random(seed)
    n = len(rows)
    samples: dict[str, list[float]] = {name: [] for name in stats}
    if n:
        for _ in range(b):
            picked = [rows[rng.randrange(n)] for _ in range(n)]
            for name, fn in stats.items():
                value = fn(picked)
                if value is not None:
                    samples[name].append(value)
    out: dict[str, tuple[float | None, float | None, float | None]] = {}
    for name, fn in stats.items():
        point = fn(rows) if n else None
        values = sorted(samples[name])
        if point is None or len(values) < 20:
            out[name] = (point, None, None)
        else:
            lo = values[int(0.025 * len(values))]
            hi = values[min(len(values) - 1, int(0.975 * len(values)))]
            out[name] = (point, lo, hi)
    return out


def ratio(num: float, den: float) -> float | None:
    return num / den if den else None
