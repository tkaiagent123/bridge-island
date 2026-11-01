"""Plain-data summaries of a finished simulation, for the CLI, JSON output and the HTML report."""

from __future__ import annotations

import csv
import io
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


TRACE_FIELDS = [
    "subsystem",
    "input",
    "source_time",
    "basis_time",
    "delivered_at",
    "age_s",
    "estimated",
]


def trace_csv_text(rows: list[dict[str, Any]]) -> str:
    """The trace as CSV text, with the standard first line labelling the numbers."""
    buffer = io.StringIO()
    buffer.write("# synthetic, illustrative\n")
    writer = csv.DictWriter(buffer, fieldnames=TRACE_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_trace_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(trace_csv_text(rows), encoding="utf-8", newline="")
    return path


def findings(summary: dict[str, Any]) -> list[str]:
    """What happened in a run, in plain sentences, for people who do not read tables."""
    out = []
    for name, s in summary["subsystems"].items():
        text = f"{name} received {s['received']} items"
        if s["mean_age_ms"] is not None:
            text += f", on average {s['mean_age_ms']:.1f} ms old when they arrived"
        extras = []
        if s["estimated"]:
            extras.append(f"{s['estimated']} of them were estimates, not real samples")
        if s["late"]:
            extras.append(f"{s['late']} arrived later than its latency budget allows")
        if s["unavailable"]:
            extras.append(
                "it was told these inputs are no longer available: " + ", ".join(s["unavailable"])
            )
        if s["issues"]:
            extras.append("it reports: " + "; ".join(s["issues"]))
        out.append(
            text + (". " + "; ".join(extras).capitalize() if extras else ", with no problems") + "."
        )
    b = summary.get("bridge")
    if b:
        counts = b["event_counts"]
        health = sorted(
            k.split(".", 1)[1]
            for k in counts
            if k.startswith("health.") and k != "health.recovered"
        )
        if counts.get("bridge.stall"):
            out.append(
                "The Bridge stalled and then resumed. The health monitor reported: "
                + (", ".join(health) or "nothing")
                + ". Data held through the stall arrives with its original, older time, so the receivers can see how old it is."
            )
        elif health:
            out.append("The health monitor reported: " + ", ".join(health) + ".")
        if b["blocked_by_cyber_monitor"]:
            out.append(
                f"The cyber monitor blocked {b['blocked_by_cyber_monitor']} message(s) that were not allowed to cross."
            )
        if b["rejected_by_translator"]:
            out.append(
                f"The Translator Module rejected {b['rejected_by_translator']} message(s) with bad or out-of-range values."
            )
        if b["estimates_delivered"]:
            out.append(
                f"{b['estimates_delivered']} estimated samples were delivered to fill gaps; they are marked as estimates and are as old as the real sample they are based on."
            )
    p = summary["platform"]
    out.append(
        f"The baseline accepted {p['accepted']} request(s) and rejected {len(p['rejected'])}."
        + (" Nothing bad reached it." if b and not p["rejected"] else "")
    )
    n = summary["network"]
    out.append(
        "No unique system has a link that skips the Bridge."
        if not n["bypass_links"]
        else f"{n['bypass_links']} link(s) skip the Bridge."
    )
    return out


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
