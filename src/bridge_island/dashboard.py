"""A dashboard view of one run: headline tiles, a status board, age bars, timeline and detail.

Everything is built from the ``summarize`` dictionary, as plain HTML with no script. Status is
always an icon plus a word, never colour alone. All numbers are synthetic, illustrative.
"""

from __future__ import annotations

import html
from typing import Any

from bridge_island.report import age_charts, table

E = html.escape

CSS = """
:root{--warn:#9a6700;--tile:#fff}
@media (prefers-color-scheme:dark){:root{--warn:#d29922;--tile:#161b22}}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem;margin:1rem 0}
.tile{border:1px solid var(--line);border-radius:8px;background:var(--tile);padding:.7rem .9rem}
.tile .k{font-size:.8rem;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
.tile .v{font-size:1.5rem;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}
.tile .u{font-size:.85rem;color:var(--muted)}
.tile.good{border-left:5px solid var(--ok)}.tile.watch{border-left:5px solid var(--warn)}
.tile.bad{border-left:5px solid var(--bad)}.tile.info{border-left:5px solid var(--s0)}
.badge{display:inline-block;font-weight:700;font-size:.85rem;padding:.1rem .55rem;border-radius:999px;
border:1px solid currentColor;white-space:nowrap}
.badge.good{color:var(--ok)}.badge.watch{color:var(--warn)}.badge.bad{color:var(--bad)}
.board{border:1px solid var(--line);border-radius:8px;background:var(--tile);margin:.5rem 0}
.sys{display:grid;grid-template-columns:minmax(9rem,13rem) 1fr;gap:.4rem 1rem;padding:.8rem 1rem;
border-top:1px solid var(--line)}.sys:first-child{border-top:0}
.sys h3{margin:0;font-size:1rem}.sys .meta{font-size:.82rem;color:var(--muted)}
.bars{display:grid;grid-template-columns:5.5rem 1fr 5rem;gap:.15rem .5rem;align-items:center;font-size:.85rem}
.bars .t{height:.8rem;background:var(--card);border-radius:0 4px 4px 0}
.bars .f{height:100%;border-radius:0 4px 4px 0;min-width:2px}
.bars .n{text-align:right;font-variant-numeric:tabular-nums}
.f.avg{background:var(--s0)}.f.peak{background:var(--s1)}
.sys .why{grid-column:2;font-size:.88rem}
.legend{display:flex;gap:1.2rem;font-size:.85rem;color:var(--muted);margin:.3rem 0}
.legend i{display:inline-block;width:.8rem;height:.8rem;border-radius:2px;margin-right:.35rem;vertical-align:-1px}
.tl{display:grid;grid-template-columns:minmax(10rem,16rem) 1fr 3rem;gap:.2rem .5rem;align-items:center;font-size:.88rem}
.tl .t{height:.7rem;background:var(--card)}.tl .f{height:100%;background:var(--s3);min-width:2px}
@media (max-width:600px){.sys{grid-template-columns:1fr}.sys .why{grid-column:1}}
"""

_ICON = {"good": "✓", "watch": "▲", "bad": "✕"}
_WORD = {"good": "OK", "watch": "Watch", "bad": "Problem"}


def _status(sub: dict[str, Any]) -> tuple[str, str]:
    """('good' | 'watch' | 'bad', the reason in a sentence)."""
    reasons = []
    if sub["issues"]:
        reasons.append("reports: " + "; ".join(sub["issues"]))
    if sub["unavailable"]:
        reasons.append("lost input: " + ", ".join(sub["unavailable"]))
    if reasons:
        return "bad", "; ".join(reasons)
    if sub["stale_s"] > 0:
        return "watch", f"{sub['stale_input']} was out of its tolerance for {sub['stale_s']:.2f} s"
    if sub["late"]:
        return "watch", f"{sub['late']} deliveries arrived older than the system allows"
    return "good", "everything arrived within its tolerance"


def _badge(level: str) -> str:
    return f'<span class="badge {level}">{_ICON[level]} {_WORD[level]}</span>'


def _tile(label: str, value: str, note: str, level: str = "info") -> str:
    return (
        f'<div class="tile {level}"><div class="k">{E(label)}</div>'
        f'<div class="v">{E(value)}</div><div class="u">{E(note)}</div></div>'
    )


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:,.1f} ms"


def _bar(kind: str, label: str, value: float | None, top: float) -> str:
    width = 0.0 if not value or top <= 0 else 100.0 * value / top
    return (
        f'<span>{label}</span><span class="t" title="{E(label)}: {_ms(value)}">'
        f'<span class="f {kind}" style="display:block;width:{width:.1f}%"></span></span>'
        f'<span class="n">{_ms(value)}</span>'
    )


def tiles(summary: dict[str, Any]) -> str:
    subs = summary["subsystems"]
    levels = {name: _status(s)[0] for name, s in subs.items()}
    bad = sum(1 for v in levels.values() if v == "bad")
    watch = sum(1 for v in levels.values() if v == "watch")
    ok = len(subs) - bad - watch
    overall = "bad" if bad else "watch" if watch else "good"
    peak_name = max(subs, key=lambda n: subs[n]["peak_age_ms"] or 0)
    peak = subs[peak_name]["peak_age_ms"]
    bridge = summary["bridge"]
    received = sum(s["received"] for s in subs.values())
    estimated = sum(s["estimated"] for s in subs.values())
    late = sum(s["late"] for s in subs.values())
    blocked = bridge["blocked_by_cyber_monitor"] + bridge["rejected_by_translator"]
    blocked += bridge["oiu_rejected_in"] + bridge["oiu_rejected_out"]
    events = sum(bridge["event_counts"].values())
    return (
        '<div class="tiles">'
        + _tile("Systems", f"{ok} of {len(subs)} OK", f"{watch} watch, {bad} problem", overall)
        + _tile("Delivered", f"{received:,}", f"{estimated:,} of them estimated")
        + _tile("Worst peak age", _ms(peak), peak_name)
        + _tile("Late", f"{late:,}", "older than a system allows", "watch" if late else "good")
        + _tile("Blocked", f"{blocked:,}", "bad or unauthorised messages", "info")
        + _tile(
            "Bridge events",
            f"{events:,}",
            "stalls, gaps, recoveries",
            "watch" if events else "good",
        )
        + "</div>"
    )


def board(summary: dict[str, Any]) -> str:
    subs = summary["subsystems"]
    top = max((s["peak_age_ms"] or 0) for s in subs.values()) or 1.0
    rows = []
    for name, s in subs.items():
        level, why = _status(s)
        rows.append(
            f'<div class="sys"><div><h3>{E(name)} {_badge(level)}</h3>'
            f'<div class="meta">{E(s["protocol"])} · {s["received"]:,} received</div></div>'
            '<div class="bars">'
            + _bar("avg", "Average", s["time_avg_age_ms"], top)
            + _bar("peak", "Peak", s["peak_age_ms"], top)
            + f'</div><div class="why muted">{E(why)}</div></div>'
        )
    return (
        '<div class="legend"><span><i style="background:var(--s0)"></i>Average age over the run</span>'
        '<span><i style="background:var(--s1)"></i>Peak age</span>'
        "<span>Same scale for every system</span></div>"
        f'<div class="board">{"".join(rows)}</div>'
    )


def timeline(summary: dict[str, Any]) -> str:
    counts = summary["bridge"]["event_counts"]
    if not counts:
        return "<p>The Bridge logged nothing unusual.</p>"
    top = max(counts.values())
    rows = "".join(
        f'<span>{E(k)}</span><span class="t"><span class="f" style="display:block;'
        f'width:{100.0 * v / top:.1f}%"></span></span><span class="n">{v}</span>'
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    return f'<div class="tl">{rows}</div>'


def detail(summary: dict[str, Any]) -> str:
    rows = [
        [
            n,
            s["protocol"],
            s["received"],
            s["estimated"],
            _ms(s["mean_age_ms"]),
            _ms(s["time_avg_age_ms"]),
            _ms(s["peak_age_ms"]),
            s["late"],
            "; ".join(s["issues"]) or "none",
        ]
        for n, s in summary["subsystems"].items()
    ]
    head = ["System", "Native bus", "Received", "Estimated", "Age at arrival", "Average age"]
    head += ["Peak age", "Late", "Problems"]
    return table(
        "Every number behind the board (synthetic, illustrative)", head, rows, numeric={2, 3, 7}
    )


def dashboard(
    summary: dict[str, Any],
    trace: list[dict[str, Any]],
    duration_s: float,
    found: str,
    events_table: str,
) -> str:
    """The whole result view, in reading order: headline, who is affected, over time, why."""
    return (
        tiles(summary)
        + "<h2>How each system fared</h2>"
        + board(summary)
        + f'<div class="card"><h2>What happened</h2><ul class="found">{found}</ul></div>'
        + "<h2>Age of the data over time</h2>"
        + '<p class="hint">Each dot is one delivery. Hollow dots are estimates.</p>'
        + age_charts("app", list(summary["subsystems"]), trace, duration_s)
        + "<h2>What the Bridge noticed</h2>"
        + timeline(summary)
        + events_table
        + "<details><summary>Show the full table</summary>"
        + detail(summary)
        + "</details>"
    )
