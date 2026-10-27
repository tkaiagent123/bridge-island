"""Interface counts for point-to-point (P2P) versus Bridge integration.

n = baseline elements, m = unique subsystems, E = dependency edges (element, subsystem).
  P2P:                    |E| interfaces, worst case n * m
  Bridge:                 n + m (one per element on the baseline side, one per subsystem)
  Bridge, consolidated:   1 + m (all baseline elements through one OIU interface)
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from types import ModuleType


@dataclass(frozen=True)
class TopologyCounts:
    n: int
    m: int
    edges: int
    p2p: int
    p2p_worst_case: int
    bridge: int
    bridge_consolidated: int


def counts(n: int, m: int, edges: int) -> TopologyCounts:
    if not 0 <= edges <= n * m:
        raise ValueError(f"{edges} edges is impossible for n={n}, m={m}")
    return TopologyCounts(n, m, edges, edges, n * m, n + m, 1 + m)


def random_edges(rng: random.Random, n: int, m: int, density: float) -> set[tuple[int, int]]:
    """Each (element, subsystem) pair is a dependency with probability ``density``."""
    return {(i, j) for i in range(n) for j in range(m) if rng.random() < density}


def sweep(
    ns: Sequence[int],
    ms: Sequence[int],
    densities: Sequence[float],
    seed: int,
    repeats: int = 5,
) -> list[dict[str, float]]:
    """One row per (n, m, density, repeat), with every count. Deterministic for a seed."""
    rng = random.Random(seed)
    rows = []
    for n in ns:
        for m in ms:
            for d in densities:
                for rep in range(repeats):
                    c = counts(n, m, len(random_edges(rng, n, m, d)))
                    rows.append({"density": d, "repeat": rep, **asdict(c)})
    return rows


def edges_from_mapping(mapping: ModuleType) -> set[tuple[str, str]]:
    """The (baseline element, subsystem) dependencies a generated mapping realizes."""
    return {(ref, sub) for ref, sub, _, _ in mapping.DOWNLINKS} | {
        (ref, sub) for ref, sub, _, _ in mapping.UNAVAILABLE
    }


def counts_for_edges(
    baseline_elements: Iterable[str], edges: set[tuple[str, str]]
) -> TopologyCounts:
    return counts(len(set(baseline_elements)), len({s for _, s in edges}), len(edges))
