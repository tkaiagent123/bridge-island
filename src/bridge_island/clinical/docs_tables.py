"""The insulin-loop tables in the docs, rendered from seeded runs (never typed by hand).

``python -m bridge_island.clinical.docs_tables`` rewrites every ``<!-- ggl:NAME:start -->`` ...
``<!-- ggl:NAME:end -->`` block in ``docs/``; ``tests/test_docs_numbers.py`` fails if a block differs
from a fresh run. Synthetic, illustrative.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bridge_island.clinical import envelope as Env
from bridge_island.clinical import experiment as X
from bridge_island.clinical import loop as L
from bridge_island.clinical import physiology as P
from bridge_island.clinical import scenarios as S
from bridge_island.clinical import traceability as T
from bridge_island.clinical.figures import SCENARIO_ORDER

DOCS = Path(__file__).resolve().parents[3] / "docs"
BLOCK = re.compile(r"(<!-- ggl:([a-z-]+):start -->\n)(.*?)(\n<!-- ggl:\2:end -->)", re.S)


def _md(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _range(s: dict[str, Any], key: str, fmt: str = "g") -> str:
    if s[f"{key}_median"] is None:
        return "-"
    return f"{s[f'{key}_median']:{fmt}} [{s[f'{key}_p5']:{fmt}}, {s[f'{key}_p95']:{fmt}}]"


def closed_loop_table(summary: Sequence[dict[str, Any]]) -> str:
    rows = []
    for s in summary:
        told = "no fault" if s["detected"] is None else f"{s['detected']} of {s['patients']}"
        if s["detected"]:
            told += f", {_range(s, 'time_to_detect_s', '.0f')} s"
        rows.append(
            (
                s["scenario"],
                s["architecture"],
                _range(s, "tir_70_180_min"),
                s["patients_below_54"],
                told,
                s["patients_unsafe_age"],
                s["unit_errors_to_controller_total"] + s["unit_errors_to_pump_total"],
                f"{s['alarms_late_total']} / {s['alarms_lost_total']}",
            )
        )
    return _md(
        (
            "Scenario",
            "Architecture",
            "Minutes in 70-180 mg/dL, median [p5, p95]",
            "Patients below 54",
            "Told: patients, time median [p5, p95]",
            "Patients acting on unflagged stale glucose",
            "Unit or encoding errors through",
            "Alarms late / lost",
        ),
        rows,
    )


def costs_table(summary: Sequence[dict[str, Any]]) -> str:
    by = {(s["scenario"], s["architecture"]): s for s in summary}
    rows = []
    for arch in L.ARCHS:
        nominal, stall = by[("nominal", arch)], by[("bridge_stall", arch)]
        rows.append(
            (
                arch,
                _range(nominal, "host_to_pump_median_s"),
                _range(nominal, "sensor_to_host_median_s"),
                nominal["failsafe_unneeded_min_median"],
                stall["paths_failed_in_stall_median"],
                _range(stall, "unsafe_age_min"),
            )
        )
    return _md(
        (
            "Architecture",
            "Controller to pump (s), nominal",
            "Sensor to controller (s), nominal",
            "Unneeded fail-safe minutes, nominal (median)",
            "Device paths dead in a 20-min stall (median)",
            "Unflagged stale-glucose minutes in the stall",
        ),
        rows,
    )


def ledger_table(rows: Sequence[dict[str, Any]]) -> str:
    out = []
    for change in dict.fromkeys(r["change"] for r in rows):
        cells: list[Any] = [change]
        for arch in L.ARCHS:
            r = next(x for x in rows if x["change"] == change and x["architecture"] == arch)
            env = (
                ""
                if r["inside_envelope"] is None
                else (", inside" if r["inside_envelope"] else ", outside")
            )
            host = ", host touched" if r["host_touched"] else ""
            cells.append(
                f"{r['components_code_change']} / {r['components_reverified']} / {r['organizations']}"
                f" / {r['tests_to_rerun']}{env}{host}"
            )
        out.append(cells)
    return _md(("Change", *(f"{a}: code / re-verify / orgs / tests" for a in L.ARCHS)), out)


def envelope_table() -> str:
    rows = []
    for c in Env.shipped_changes():
        d = Env.check(c)
        rows.append(
            (
                c.path.stem if c.path else c.name,
                "inside" if d.inside else "outside",
                len(d.tests) if d.inside else "-",
                "; ".join(o for o, _ in d.owners),
            )
        )
    return _md(
        ("Change", "Envelope", "Conformance tests to re-run", "Organizations to involve"), rows
    )


def cohort_line(seed: int = 1) -> str:
    kept, rejected = P.cohort(seed)
    return (
        f"Seed {seed} drew {len(kept) + rejected} parameter sets and kept {len(kept)}; {rejected} did not "
        "settle back to their resting glucose within 8 hours and were rejected."
    )


def traceability_counts() -> str:
    c = T.counts()
    return ", ".join(f"{n} {status}" for status, n in c.items())


def render(seed: int = 1, workers: int | None = None) -> dict[str, str]:
    rows, _ = X.run_matrix([p.stem for p in S.shipped()], L.ARCHS, range(30), seed, workers)
    summary = X.summarize(rows)
    order = {name: i for i, name in enumerate(SCENARIO_ORDER)}
    summary.sort(key=lambda s: (order.get(s["scenario"], 99), L.ARCHS.index(s["architecture"])))
    return {
        "closed-loop": closed_loop_table(summary),
        "costs": costs_table(summary),
        "ledger": ledger_table(X.ledger(seed)),
        "envelope": envelope_table(),
        "cohort": cohort_line(seed),
        "traceability-counts": traceability_counts(),
        "traceability": T.markdown(),
    }


def blocks(text: str) -> dict[str, str]:
    return {m.group(2): m.group(3) for m in BLOCK.finditer(text)}


def refresh(tables: dict[str, str]) -> list[Path]:
    changed = []
    for page in sorted(DOCS.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        new = BLOCK.sub(
            lambda m: m.group(1) + tables.get(m.group(2), m.group(3)) + m.group(4), text
        )
        if new != text:
            page.write_text(new, encoding="utf-8", newline="\n")
            changed.append(page)
    return changed


if __name__ == "__main__":
    for path in refresh(render()):
        print(f"updated {path}")
    sys.exit(0)
