"""The insulin-loop experiments: ``closed-loop`` and ``change-ledger``. Synthetic, illustrative.

closed-loop: every shipped scenario x the three architectures x the 30 virtual patients, the same
patients, meals, seeds and faults in every architecture. Results are counts and medians with the
5th-95th percentile across the cohort. No p-values and no claim of clinical significance.

change-ledger: the ten declared changes x the three architectures, counted with the model stated in
``analysis/change_impact.py`` (with a sensitivity table), and the envelope decision for each.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from bridge_island.analysis import change_impact as ci
from bridge_island.clinical import envelope as E
from bridge_island.clinical import loop as L
from bridge_island.clinical import metrics as M
from bridge_island.clinical import scenarios as S

LABEL = "synthetic, illustrative"
GLUCOSE = ("tir_70_180_min", "below_70_min", "below_54_min", "above_180_min", "above_250_min")
SUMMED = (
    "unit_errors_to_controller",
    "unit_errors_to_pump",
    "commands_unconfirmed",
    "alarms_raised",
    "alarms_on_time",
    "alarms_late",
    "alarms_lost",
)
MEDIANS = (
    *GLUCOSE,
    "unsafe_age_min",
    "delivered_minus_commanded_u",
    "sensor_to_host_median_s",
    "host_to_pump_median_s",
    "failsafe_min",
    "failsafe_unneeded_min",
)


def run_one(
    job: tuple[L.LoopScenario, str, int, int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One (scenario, architecture, patient index, seed): the result row and, for
    bridge-contract, the critical event record."""
    scenario, arch, index, seed = job
    if seed != scenario.seed:
        scenario = L.LoopScenario(
            scenario.name, scenario.description, scenario.duration_s, seed, scenario.faults
        )
    loop = L.build_loop(arch, L.patients(seed)[index], scenario)
    loop.run()
    row = M.measure(loop)
    events = []
    if arch == "bridge-contract":
        events = [{"patient": loop.params.id, **e} for e in M.critical_events(loop)]
    return row, events


def run_matrix(
    scenarios: Sequence[str | L.LoopScenario],
    archs: Sequence[str],
    patients: Sequence[int],
    seed: int,
    workers: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Every scenario (a shipped name, or a parsed scenario) x architecture x patient."""
    resolved = [S.by_name(s) if isinstance(s, str) else s for s in scenarios]
    jobs = [(sc, a, p, seed) for sc in resolved for a in archs for p in patients]
    workers = workers if workers is not None else max(1, min(4, (os.cpu_count() or 1)))
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(workers) as pool:
            results = list(pool.map(run_one, jobs, chunksize=4))
    else:
        results = [run_one(j) for j in jobs]
    rows = [r for r, _ in results]
    events: dict[str, list[dict[str, Any]]] = {}
    for (sc, _, _, _), (_, ev) in zip(jobs, results, strict=True):
        if ev:
            events.setdefault(sc.name, []).extend(ev)
    return rows, events


def _q(values: list[float]) -> dict[str, float]:
    s = ci.summarize([{"v": v} for v in values], "v")
    return {"median": s["p50"], "p5": s["p5"], "p95": s["p95"]}


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (scenario, architecture): counts, and median [p5, p95] across the cohort."""
    out = []
    keys = list(dict.fromkeys((r["scenario"], r["architecture"]) for r in rows))
    for scenario, arch in keys:
        group = [r for r in rows if r["scenario"] == scenario and r["architecture"] == arch]
        row: dict[str, Any] = {"scenario": scenario, "architecture": arch, "patients": len(group)}
        detected = [r for r in group if r["detected"]]
        row["fault"] = group[0]["detected"] is not None
        row["detected"] = len(detected) if row["fault"] else None
        times = [r["time_to_detect_s"] for r in detected]
        for k, v in (_q(times) if times else {"median": None, "p5": None, "p95": None}).items():
            row[f"time_to_detect_s_{k}"] = v
        for key in MEDIANS:
            values = [r[key] for r in group if r[key] is not None]
            for k, v in (
                _q(values) if values else {"median": None, "p5": None, "p95": None}
            ).items():
                row[f"{key}_{k}"] = v
        for key in SUMMED:
            row[f"{key}_total"] = sum(r[key] for r in group)
        row["patients_unsafe_age"] = sum(1 for r in group if r["unsafe_age_min"] > 0)
        row["patients_below_54"] = sum(1 for r in group if r["below_54_min"] > 0)
        row["host_to_pump_peak_s"] = max((r["host_to_pump_peak_s"] or 0) for r in group)
        stall = [
            r["paths_failed_in_stall"] for r in group if r["paths_failed_in_stall"] is not None
        ]
        row["paths_failed_in_stall_median"] = _q(stall)["median"] if stall else None
        out.append(row)
    return out


def closed_loop(
    out: Path,
    seed: int = 1,
    names: Sequence[str] | None = None,
    patients: Sequence[int] | None = None,
    workers: int | None = None,
    figures: bool = True,
) -> dict[str, Path]:
    from bridge_island.experiments import _table, _write_csv

    names = list(names or [p.stem for p in S.shipped()])
    patients = list(patients if patients is not None else range(30))
    started = time.perf_counter()
    rows, events = run_matrix(names, L.ARCHS, patients, seed, workers)
    elapsed = time.perf_counter() - started
    summary = summarize(rows)
    files = {
        "runs": _write_csv(out / "closed_loop_runs.csv", rows),
        "summary": _write_csv(out / "closed_loop_summary.csv", summary),
    }
    for name, ev in events.items():
        files[f"events_{name}"] = _write_csv(out / f"critical_events_{name}.csv", ev)
    print(
        f"Closed loop ({LABEL}): {len(names)} scenarios x 3 architectures x {len(patients)} virtual "
        f"patients, seed {seed}; {len(rows)} runs in {elapsed:.0f} s"
    )
    _table(
        [
            (
                "scenario",
                "architecture",
                "time 70-180 min",
                "below 54 min",
                "detected",
                "unsafe-age min",
            )
        ]
        + [
            (
                s["scenario"],
                s["architecture"],
                f"{s['tir_70_180_min_median']:g} [{s['tir_70_180_min_p5']:g}, {s['tir_70_180_min_p95']:g}]",
                f"{s['patients_below_54']} pts",
                "-" if s["detected"] is None else f"{s['detected']} of {s['patients']}",
                f"{s['patients_unsafe_age']} pts",
            )
            for s in summary
        ]
    )
    if figures:
        from bridge_island.clinical import figures as F

        files.update(F.closed_loop_figures(out, rows, summary, seed))
    return files


def ledger(seed: int = 1, adapters_in_host: bool = True) -> list[dict[str, Any]]:
    world = L.world()
    suite_size = len(E.suite())
    rows = []
    for change in E.shipped_changes():
        decision = E.check(change)
        for arch in L.ARCHS:
            r = ci.ledger_row(
                arch,
                change.side,
                frozenset(change.declares),
                world.OWNERS[change.device],
                world.OWNERS["ControllerHost"],
                E.INTEGRATOR,
                tuple(o for o, _ in decision.owners),
                decision.inside,
                len(decision.tests),
                suite_size,
                adapters_in_host,
            )
            rows.append(
                {
                    "change": change.path.stem if change.path else change.name,
                    "description": change.name,
                    "architecture": arch,
                    **vars(r),
                    "owners_to_involve": "; ".join(o for o, _ in decision.owners)
                    if arch == "bridge-contract"
                    else "",
                }
            )
    return rows


def change_ledger(out: Path, seed: int = 1, figures: bool = True) -> dict[str, Path]:
    from bridge_island.experiments import _table, _write_csv

    rows = ledger(seed)
    files = {"csv": _write_csv(out / "change_ledger.csv", rows)}
    decisions = [E.check(c).as_dict() for c in E.shipped_changes()]
    files["decisions"] = _write_csv(
        out / "change_envelope_decisions.csv",
        [
            {
                "change": d["change"],
                "device": d["device"],
                "inside_envelope": d["inside_envelope"],
                "tests_to_rerun": len(d["tests_to_rerun"]),
                "owners_to_involve": "; ".join(o["organization"] for o in d["owners_to_involve"]),
                "why": " | ".join(d["reasons"]),
            }
            for d in decisions
        ],
    )
    sensitivity = []
    for in_host in (True, False):
        for r in ledger(seed, in_host):
            if r["architecture"] == "direct":
                sensitivity.append(
                    {
                        "direct_adapters_live_in": "host" if in_host else "device",
                        "change": r["change"],
                        "components_code_change": r["components_code_change"],
                        "components_reverified": r["components_reverified"],
                        "organizations": r["organizations"],
                        "host_touched": r["host_touched"],
                    }
                )
    files["sensitivity"] = _write_csv(out / "change_ledger_sensitivity.csv", sensitivity)
    print(f"Change ledger ({LABEL}): 10 declared changes x 3 architectures, seed {seed}")
    _table(
        [
            (
                "change",
                "architecture",
                "code",
                "re-verify",
                "orgs",
                "envelope",
                "tests",
                "host touched",
            )
        ]
        + [
            (
                r["change"],
                r["architecture"],
                r["components_code_change"],
                r["components_reverified"],
                r["organizations"],
                "-"
                if r["inside_envelope"] is None
                else ("inside" if r["inside_envelope"] else "outside"),
                r["tests_to_rerun"],
                "yes" if r["host_touched"] else "no",
            )
            for r in rows
        ]
    )
    print(
        "  Counts follow the stated model (analysis/change_impact.py); they are not measurements."
    )
    if figures:
        from bridge_island.clinical import figures as F

        files.update(F.ledger_figure(out, rows))
        files.update(F.traceability_figure(out))
    return files
