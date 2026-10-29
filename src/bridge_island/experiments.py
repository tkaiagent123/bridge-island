"""The four reproducible experiments. Seeded, CSV output, optional plots.

Every number written or printed here is synthetic and illustrative. These experiments
demonstrate a structural argument on invented platforms; they are not program data.
"""

from __future__ import annotations

import csv
import itertools
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from bridge_island.analysis import change_impact as ci
from bridge_island.analysis import topology
from bridge_island.codegen import load_module
from bridge_island.examples import generated_path

LABEL = "synthetic, illustrative"


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# {LABEL}\n")
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _plt() -> Any | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    return plt


def _save(fig: Any, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    return path


def _table(rows: Iterable[Sequence[Any]]) -> None:
    grid = [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in grid) + 2 for i in range(len(grid[0]))]
    for row in grid:
        print("  " + "".join(c.ljust(w) for c, w in zip(row, widths, strict=True)))


# --- 1. topology sweep ------------------------------------------------------------------


def topology_sweep(out: Path, seed: int) -> dict[str, Path]:
    rows = topology.sweep([5, 10, 20, 40, 80], [2, 4, 8, 16], [0.1, 0.3, 0.6], seed, repeats=5)
    files = {"csv": _write_csv(out / "topology_sweep.csv", rows)}
    n, d = 40, 0.3
    pick = [r for r in rows if r["n"] == n and r["density"] == d]
    by_m: dict[int, list[dict[str, float]]] = {}
    for r in pick:
        by_m.setdefault(int(r["m"]), []).append(r)

    def mean(rs: list[dict[str, float]], k: str) -> float:
        return sum(r[k] for r in rs) / len(rs)

    print(f"Topology sweep ({LABEL}): n={n} baseline elements, dependency density {d}")
    _table(
        [("m subsystems", "P2P |E|", "P2P worst n*m", "Bridge n+m", "Bridge 1+m")]
        + [
            (
                m,
                f"{mean(by_m[m], 'p2p'):.1f}",
                by_m[m][0]["p2p_worst_case"],
                by_m[m][0]["bridge"],
                by_m[m][0]["bridge_consolidated"],
            )
            for m in sorted(by_m)
        ]
    )
    plt = _plt()
    if plt:
        ms = sorted(by_m)
        fig, ax = plt.subplots(figsize=(6, 4))
        for key, label in [
            ("p2p_worst_case", "P2P worst case n*m"),
            ("p2p", "P2P |E| (mean)"),
            ("bridge", "Bridge n+m"),
            ("bridge_consolidated", "Bridge 1+m"),
        ]:
            ax.plot(ms, [mean(by_m[m], key) for m in ms], marker="o", label=label)
        ax.set(
            xlabel="unique subsystems m",
            ylabel="interfaces",
            title=f"Interface count, n={n}, density {d} ({LABEL})",
        )
        ax.legend()
        files["plot"] = _save(fig, out / "topology_sweep.png")
    return files


# --- 2. change impact -------------------------------------------------------------------


def change_impact(out: Path, seed: int, world: ModuleType, trials: int) -> dict[str, Path]:
    first, last = world.VERSIONS[0], world.VERSIONS[-1]
    changes = ci.diff_interfaces(world.interface(first), world.interface(last), world.RENAMES)
    edges = topology.edges_from_mapping(load_module(generated_path(world, first)))
    p2p, bridge = ci.impact_p2p(changes, edges), ci.impact_bridge(changes, edges)
    metrics = list(vars(p2p))
    rows = [{"metric": m, "p2p": getattr(p2p, m), "bridge": getattr(bridge, m)} for m in metrics]
    files = {
        "changes": _write_csv(
            out / "change_set.csv", [{"kind": c.kind, "ref": c.ref} for c in changes]
        ),
        "csv": _write_csv(out / "change_impact.csv", rows),
    }
    print(f"Change impact, {world.NAME} v{first} -> v{last} ({LABEL}); seed {seed}")
    _table([("", "P2P", "Bridge")] + [(r["metric"], r["p2p"], r["bridge"]) for r in rows])
    print(
        "  Counts are element-level dependencies (n = baseline data elements, not systems)."
        "\n  'tests_failing' assumes 1 conformance test per dependency; that value is arbitrary."
        " Sensitivity:"
    )
    sensitivity = [
        {
            "tests_per_edge": k,
            "p2p_tests_failing": ci.impact_p2p(changes, edges, k).tests_failing,
            "bridge_tests_failing": ci.impact_bridge(changes, edges, k).tests_failing,
        }
        for k in (0, 1, 2, 5)
    ]
    files["sensitivity"] = _write_csv(out / "change_impact_sensitivity.csv", sensitivity)
    _table(
        [("tests per dependency", "P2P tests failing", "Bridge tests failing")]
        + [
            (s["tests_per_edge"], s["p2p_tests_failing"], s["bridge_tests_failing"])
            for s in sensitivity
        ]
    )

    mc = ci.monte_carlo(seed, trials)
    files["monte_carlo"] = _write_csv(out / "change_impact_monte_carlo.csv", mc)
    summary = []
    for m in metrics:
        for arch in ("p2p", "bridge"):
            summary.append({"metric": m, "architecture": arch, **ci.summarize(mc, f"{arch}_{m}")})
    files["summary"] = _write_csv(out / "change_impact_distributions.csv", summary)
    print(
        f"Monte Carlo over {trials} random architectures and change sets ({LABEL}); mean [p5, p95]:"
    )

    def cell(arch: str, m: str) -> str:
        s = ci.summarize(mc, f"{arch}_{m}")
        return f"{s['mean']:.1f} [{s['p5']:.0f}, {s['p95']:.0f}]"

    _table(
        [("metric", "P2P", "Bridge")] + [(m, cell("p2p", m), cell("bridge", m)) for m in metrics]
    )
    plt = _plt()
    if plt:
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), sharey=True)
        for ax, key, title in zip(
            axes,
            ("subsystems_code_change", "components_reverified"),
            ("subsystems needing code change", "components re-verified"),
            strict=True,
        ):
            top = int(max(r[f"p2p_{key}"] for r in mc)) + 2
            ax.hist([r[f"p2p_{key}"] for r in mc], bins=range(top), alpha=0.6, label="P2P")
            ax.hist([r[f"bridge_{key}"] for r in mc], bins=range(top), alpha=0.6, label="Bridge")
            ax.set(title=title, xlabel="count per trial")
            ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
        axes[0].set_ylabel("trials")
        axes[0].legend()
        fig.suptitle(f"Monte Carlo, {trials} trials ({LABEL})")
        files["plot"] = _save(fig, out / "change_impact.png")
    return files


# --- 3. timing and continuity -----------------------------------------------------------


def timing(out: Path, seed: int, world: ModuleType) -> dict[str, Path]:
    version = world.VERSIONS[0]
    slow_sub, slow_native = world.TIMING["slow"]
    fast_sub, fast_native = world.TIMING["fast"]
    start, duration = world.TIMING["stall"]
    rows: list[dict[str, Any]] = []
    summary = []
    for mode in ("source", "delivery"):
        system = world.build(version, seed, time_tag=mode)
        assert system.bridge is not None
        system.bridge.stall(start, duration)
        system.run(start + duration + 3.0)
        for sub, native in ((slow_sub, slow_native), (fast_sub, fast_native)):
            for r in system.subsystems[sub].received:
                if r.native_name == native:
                    rows.append(
                        {
                            "time_tag": mode,
                            "subsystem": sub,
                            "input": native,
                            "source_time": round(r.datum.source_time, 6),
                            "delivered_at": round(r.delivered_at, 6),
                            "age_s": round(r.age, 6),
                        }
                    )
        slow = [r for r in system.subsystems[slow_sub].received if r.native_name == slow_native]
        fast = [r for r in system.subsystems[fast_sub].received if r.native_name == fast_native]
        times = [r.datum.source_time for r in fast]
        kinds = [e.kind for e in system.bridge.log.events if e.kind.startswith("health.")]
        summary.append(
            {
                "time_tag": mode,
                "max_age_s_of_slow_input": round(max(r.age for r in slow), 3),
                "slow_input_flagged_late": sum(
                    1 for r in system.subsystems[slow_sub].late if r.native_name == slow_native
                ),
                "largest_gap_in_fast_input_s": round(
                    max(b - a for a, b in itertools.pairwise(times)), 3
                ),
                "health_events": ";".join(sorted(set(k.removeprefix("health.") for k in kinds))),
                "annunciations_at_slow_subsystem": len(system.subsystems[slow_sub].annunciations),
            }
        )
    files = {
        "csv": _write_csv(out / "timing_ages.csv", rows),
        "summary": _write_csv(out / "timing_summary.csv", summary),
    }
    print(
        f"Timing and continuity, {world.NAME} ({LABEL}); Bridge stalls at {start} s for {duration} s"
    )
    _table([tuple(summary[0])] + [tuple(s.values()) for s in summary])
    print(
        "  With source time (G3) the held message arrives visibly old and is flagged late; with delivery-time stamping it looks fresh."
    )
    plt = _plt()
    if plt:
        fig, ax = plt.subplots(figsize=(6.5, 4))
        for mode in ("source", "delivery"):
            pts = [
                (r["delivered_at"], r["age_s"])
                for r in rows
                if r["time_tag"] == mode and r["subsystem"] == slow_sub
            ]
            # markers only: no datum is delivered while the Bridge is stalled
            ax.plot(*zip(*pts, strict=True), "o", label=f"stamped with {mode} time")
        ax.axvspan(start, start + duration, alpha=0.15, label="Bridge stalled")
        ax.set(
            xlabel="delivery time (s)",
            ylabel="data age seen by consumer (s)",
            title=f"{slow_sub}.{slow_native} ({LABEL})",
        )
        ax.legend()
        files["plot"] = _save(fig, out / "timing.png")
    return files


# --- 4. fault containment ---------------------------------------------------------------


def containment(out: Path, seed: int, world: ModuleType) -> dict[str, Path]:
    version = world.VERSIONS[0]
    cfg = world.CONTAINMENT
    accepted = world.interface(version).accepted[0]
    raw_bad = {e.name: 0 for e in accepted.elements} | {accepted.elements[0].name: 99}
    scenarios = {
        "valid message": ("send", cfg["message"], cfg["good"]),
        "out-of-range value": ("send", cfg["message"], cfg["out_of_range"]),
        "bad enum value": ("send", cfg["message"], cfg["bad_enum"]),
        "malformed (missing field)": ("send", cfg["message"], cfg["malformed"]),
        "unauthorized message type": ("raw", cfg["unauthorized"], {}),
        "baseline-format message, out of range": ("raw", accepted.name, raw_bad),
    }
    rows: list[dict[str, Any]] = []
    for name, (how, message, values) in scenarios.items():
        for arch in ("bridge", "p2p"):
            system = (
                world.build(version, seed) if arch == "bridge" else world.build_p2p(version, seed)
            )
            system.run(1.0)
            sender = system.subsystems[cfg["sender"]]
            if arch == "bridge" or how == "send":
                sender.send(message, values)  # the only path a subsystem has on the Bridge
            else:
                assert system.adapters is not None
                system.adapters[cfg["sender"]].send_raw(message, values)  # faulty software on P2P
            system.run(3.0)
            log = system.bridge.log if system.bridge else None
            rows.append(
                {
                    "scenario": name,
                    "architecture": arch,
                    "baseline_accepted": len(system.platform.accepted),
                    "baseline_rejected": len(system.platform.rejected),
                    "blocked_by_cyber_monitor": len(log.of_kind("cyber.block")) if log else 0,
                    "rejected_by_translator": len(log.of_kind("tm.reject_up")) if log else 0,
                    "caught_by_subsystem_adapter": sum(
                        len(a.errors) for a in system.adapters.values()
                    )
                    if system.adapters
                    else 0,
                    "logged_by_bridge": bool(
                        log and (log.of_kind("cyber.block") or log.of_kind("tm.reject_up"))
                    ),
                }
            )
    files = {"csv": _write_csv(out / "containment.csv", rows)}
    print(f"Fault containment, {world.NAME} ({LABEL}): faulty sender {cfg['sender']}")
    header = ("scenario", "architecture", "baseline accepted", "baseline rejected")
    _table(
        [(*header, "Bridge blocked", "caught by subsystem code")]
        + [
            (
                r["scenario"],
                r["architecture"],
                r["baseline_accepted"],
                r["baseline_rejected"],
                r["blocked_by_cyber_monitor"] + r["rejected_by_translator"],
                r["caught_by_subsystem_adapter"],
            )
            for r in rows
        ]
    )
    assert all(r["baseline_rejected"] == 0 for r in rows if r["architecture"] == "bridge")
    print(
        "  On the Bridge the baseline never sees a bad message. On P2P only each subsystem's own"
        " code stands in the way; when that code is faulty, the baseline must reject the message."
    )
    return files


def run(name: str, world: ModuleType, seed: int, out: Path, trials: int = 1000) -> dict[str, Path]:
    out = out / name
    if name == "topology":
        files = topology_sweep(out, seed)
    elif name == "change-impact":
        files = change_impact(out, seed, world, trials)
    elif name == "timing":
        files = timing(out, seed, world)
    elif name == "containment":
        files = containment(out, seed, world)
    else:
        raise KeyError(name)
    for path in files.values():
        print(f"wrote {path}")
    return files
