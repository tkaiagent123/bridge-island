"""Plain-data summaries of a finished simulation, for the CLI, JSON output and the HTML report."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from bridge_island.system import System


def _ms(seconds: float) -> float:
    return round(1000.0 * seconds, 3)


def summarize(system: System) -> dict[str, Any]:
    """Counts, ages and issues for every subsystem, plus Bridge, baseline and network state."""
    subsystems: dict[str, Any] = {}
    for name, sub in system.subsystems.items():
        ages = [r.age for r in sub.received]
        subsystems[name] = {
            "protocol": sub.protocol,
            "inputs": len(sub.needs),
            "received": len(sub.received),
            "estimated": sum(1 for r in sub.received if r.datum.estimated),
            "mean_age_ms": _ms(sum(ages) / len(ages)) if ages else None,
            "max_age_ms": _ms(max(ages)) if ages else None,
            "late": len(sub.late),
            "issues": sub.check(),
            "unavailable": dict(sub.unavailable),
            "annunciations": [
                {"time_s": round(t, 6), "kind": kind, "detail": detail}
                for t, kind, detail in sub.annunciations
            ],
        }
    out: dict[str, Any] = {"time_s": round(system.sched.now, 6), "subsystems": subsystems}
    bridge = system.bridge
    if bridge is not None:
        kinds: dict[str, int] = {}
        for e in bridge.log.events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        out["bridge"] = {
            "identity_paths_used": bridge.tm.identity_count,
            "translated_paths_used": bridge.tm.translated_count,
            "decimated": bridge.uid.decimated,
            "estimates_delivered": bridge.uid.estimates,
            "blocked_by_cyber_monitor": bridge.cyber.blocked,
            "rejected_by_translator": bridge.tm.rejected,
            "oiu_rejected_in": bridge.oiu.rejected_in,
            "oiu_rejected_out": bridge.oiu.rejected_out,
            "event_counts": dict(sorted(kinds.items())),
            "events": [
                {"time_s": round(e.time, 6), "source": e.source, "kind": e.kind, "detail": e.detail}
                for e in bridge.log.events
            ],
        }
    platform = system.platform
    out["platform"] = {
        "accepted": len(platform.accepted),
        "rejected": [{"time_s": round(t, 6), "reason": why} for t, why in platform.rejected],
        "emitted": dict(platform.emitted),
    }
    out["network"] = {
        "links": len(system.network.links),
        "bypass_links": len(system.network.bypass_links()),
        "problems": system.network.problems(),
    }
    return out


def trace_rows(system: System) -> list[dict[str, Any]]:
    """Every datum every subsystem received, with its source time, delivery time and age."""
    rows = []
    for name, sub in system.subsystems.items():
        for r in sub.received:
            d = r.datum
            rows.append(
                {
                    "subsystem": name,
                    "input": r.native_name,
                    "source_time": round(d.source_time, 6),
                    "basis_time": "" if d.basis_time is None else round(d.basis_time, 6),
                    "delivered_at": round(r.delivered_at, 6),
                    "age_s": round(r.age, 6),
                    "estimated": d.estimated,
                }
            )
    rows.sort(key=lambda r: (r["delivered_at"], r["subsystem"], r["input"]))
    return rows


def write_trace_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write the trace as CSV, with the standard first line labelling the numbers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        list(rows[0])
        if rows
        else [
            "subsystem",
            "input",
            "source_time",
            "basis_time",
            "delivered_at",
            "age_s",
            "estimated",
        ]
    )
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write("# synthetic, illustrative\n")
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def render_text(summary: dict[str, Any], max_events: int = 12) -> str:
    """The summary as a plain-text table, for the terminal."""
    lines = [
        f"{'subsystem':<17}{'native bus':<18}{'received':>9}{'estimated':>10}{'mean age':>10}{'late':>6}  issues"
    ]
    for name, s in summary["subsystems"].items():
        mean = f"{s['mean_age_ms']:.1f} ms" if s["mean_age_ms"] is not None else "-"
        issues = "; ".join(s["issues"]) or "none"
        lines.append(
            f"{name:<17}{s['protocol']:<18}{s['received']:>9}{s['estimated']:>10}{mean:>10}{s['late']:>6}  {issues}"
        )
    b = summary.get("bridge")
    if b:
        lines.append(
            f"Bridge: {b['identity_paths_used']} identity, {b['translated_paths_used']} translated, "
            f"{b['decimated']} decimated, {b['estimates_delivered']} estimates, "
            f"{b['blocked_by_cyber_monitor']} blocked by the cyber monitor, "
            f"{b['rejected_by_translator']} rejected by the translator"
        )
        shown = [e for e in b["events"] if not e["kind"].startswith("health.out")][:max_events]
        for e in shown:
            lines.append(f"  {e['time_s']:>7.3f} s  {e['kind']:<20}{e['detail']}")
        if len(b["events"]) > len(shown):
            lines.append(f"  ... {len(b['events']) - len(shown)} more events (use --json for all)")
    p = summary["platform"]
    lines.append(f"Baseline: {p['accepted']} inputs accepted, {len(p['rejected'])} rejected")
    n = summary["network"]
    lines.append(f"Links that skip the Bridge: {n['bypass_links']}")
    return "\n".join(lines)
