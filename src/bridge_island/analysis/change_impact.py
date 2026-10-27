"""What a baseline interface change touches, in each architecture.

Counting model (stated assumptions, not measurements):
  * The unit of counting is the element-level dependency: an edge (baseline data element,
    subsystem). A count taken at another level, for example baseline systems or units, is a
    different quantity and is not comparable with these.
  * Every dependency edge has ``tests_per_edge`` conformance tests (default 1), located in
    whatever component owns the adapter for that edge. Every subsystem also has one
    acceptance test. The value of ``tests_per_edge`` is arbitrary and it alone drives the
    size of the "tests failing" row; the *difference* between the architectures in that row
    does not depend on it (it is the number of P2P subsystems with code changes minus the
    number of Bridge subsystems with code changes).
  * P2P: each subsystem owns its own adapter module. A rename, unit, encoding or removal
    change needs code in every adapter that reads the element. A rate change needs no code
    but invalidates timing assumptions, so those subsystems are re-verified.
  * Bridge: the adapters are entries in one mapping, so those changes touch the Translator
    Module only. A removed element is the exception: the affected subsystem must also handle
    the "unavailable" report. The Bridge itself is re-verified after any baseline change.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from bridge_island.baseline.platform import BaselineInterface

Kind = Literal["rename", "unit", "encoding", "rate", "add", "remove"]
KINDS: tuple[Kind, ...] = ("rename", "unit", "encoding", "rate", "add", "remove")
VALUE_KINDS = frozenset({"rename", "unit", "encoding", "remove"})  # change what a value means
Edge = tuple[str, str]  # (baseline element ref "Message.field", subsystem)


@dataclass(frozen=True)
class Change:
    """``ref`` is an element ref, or the message name for a rate change."""

    kind: Kind
    ref: str


@dataclass(frozen=True)
class Impact:
    subsystems_code_change: int
    modules_touched: int
    tests_failing: int
    components_reverified: int


def diff_interfaces(
    old: BaselineInterface, new: BaselineInterface, renames: Mapping[str, str] | None = None
) -> list[Change]:
    """Changes between two baseline versions. Renames cannot be inferred, so they are given."""
    renames = renames or {}
    changes: list[Change] = []
    old_refs = [f"{m.name}.{e.name}" for m in old.published for e in m.elements]
    new_refs = [f"{m.name}.{e.name}" for m in new.published for e in m.elements]
    for ref in old_refs:
        target = renames.get(ref, ref)
        a, b = old.element(ref), new.element(target)
        if ref in renames:
            changes.append(Change("rename", ref))
        if b is None:
            changes.append(Change("remove", ref))
        elif a is not None:
            ra, rb = a.representation, b.representation
            if ra.units != rb.units:
                changes.append(Change("unit", ref))
            if (ra.encoding, ra.frame) != (rb.encoding, rb.frame):
                changes.append(Change("encoding", ref))
    known = set(old_refs) | set(renames.values())
    changes += [Change("add", ref) for ref in new_refs if ref not in known]
    for spec in old.published:
        other = new.published_message(spec.name)
        if other is not None and other.rate_hz != spec.rate_hz:
            changes.append(Change("rate", spec.name))
    return changes


def impact_p2p(changes: list[Change], edges: set[Edge], tests_per_edge: int = 1) -> Impact:
    value_refs = {c.ref for c in changes if c.kind in VALUE_KINDS}
    hit = [e for e in edges if e[0] in value_refs]
    coded = {s for _, s in hit}
    rate_msgs = {c.ref for c in changes if c.kind == "rate"}
    timing = {s for ref, s in edges if ref.split(".")[0] in rate_msgs}
    failing = tests_per_edge * len(hit) + len(coded)
    return Impact(len(coded), len(coded), failing, len(coded | timing))


def impact_bridge(changes: list[Change], edges: set[Edge], tests_per_edge: int = 1) -> Impact:
    value_refs = {c.ref for c in changes if c.kind in VALUE_KINDS}
    removed = {c.ref for c in changes if c.kind == "remove"}
    hit = [e for e in edges if e[0] in value_refs]
    removal_subs = {s for ref, s in edges if ref in removed}
    tm = 1 if hit else 0  # one mapping module, however many edges changed
    return Impact(
        len(removal_subs),
        tm + len(removal_subs),
        tests_per_edge * len(hit) + len(removal_subs),
        (1 if changes else 0) + len(removal_subs),
    )


# --- Monte Carlo over random architectures and change sets -----------------------------


def random_scenario(
    rng: random.Random, n: int, m: int, density: float, n_changes: int, per_message: int = 4
) -> tuple[set[Edge], list[Change]]:
    refs = [f"M{i // per_message}.e{i}" for i in range(n)]
    edges = {(refs[i], f"S{j}") for i in range(n) for j in range(m) if rng.random() < density}
    changes: list[Change] = []
    for k in range(n_changes):
        kind = rng.choice(KINDS)
        if kind == "add":
            changes.append(Change("add", f"M{n // per_message}.new{k}"))
        elif kind == "rate":
            changes.append(Change("rate", f"M{rng.randrange((n - 1) // per_message + 1)}"))
        else:
            changes.append(Change(kind, rng.choice(refs)))
    return edges, changes


def monte_carlo(
    seed: int,
    trials: int = 1000,
    n_range: tuple[int, int] = (10, 100),
    m_range: tuple[int, int] = (3, 20),
    density_range: tuple[float, float] = (0.1, 0.6),
    changes_range: tuple[int, int] = (1, 6),
) -> list[dict[str, float]]:
    """One row per trial with the impact in both architectures. Deterministic for a seed."""
    rng = random.Random(seed)
    rows = []
    for t in range(trials):
        n, m = rng.randint(*n_range), rng.randint(*m_range)
        density = rng.uniform(*density_range)
        edges, changes = random_scenario(rng, n, m, density, rng.randint(*changes_range))
        p2p, br = impact_p2p(changes, edges), impact_bridge(changes, edges)
        rows.append(
            {
                "trial": t,
                "n": n,
                "m": m,
                "density": round(density, 3),
                "changes": len(changes),
                **{f"p2p_{k}": v for k, v in vars(p2p).items()},
                **{f"bridge_{k}": v for k, v in vars(br).items()},
            }
        )
    return rows


def summarize(rows: list[dict[str, float]], key: str) -> dict[str, float]:
    """Mean and 5th/50th/95th percentiles of one column."""
    values = sorted(r[key] for r in rows)

    def pct(p: float) -> float:
        return values[min(len(values) - 1, int(p * len(values)))]

    return {"mean": sum(values) / len(values), "p5": pct(0.05), "p50": pct(0.5), "p95": pct(0.95)}
