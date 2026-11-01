"""`bridge-island report`: one self-contained HTML file for an example.

No scripts and no external resources: it opens offline and prints. Charts are inline SVG with a
title and description, and they encode series by marker shape as well as colour.
"""

from __future__ import annotations

import html
import json
import math
from types import ModuleType
from typing import Any

import bridge_island
from bridge_island.describe import describe
from bridge_island.diffing import diff_versions
from bridge_island.rules import check_rules
from bridge_island.scenario import Scenario, manifest
from bridge_island.summary import summarize, trace_rows

E = html.escape
SYNTHETIC = "All content is invented. Numbers are synthetic, illustrative."

CSS = """
:root{--bg:#fff;--fg:#1b1f24;--muted:#57606a;--line:#d0d7de;--ok:#1a7f37;--bad:#cf222e;--card:#f6f8fa;
--s0:#0969da;--s1:#bc4c00;--s2:#1a7f37;--s3:#8250df;--s4:#cf222e;--s5:#6e7781}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;
--ok:#3fb950;--bad:#ff7b72;--card:#161b22;--s0:#58a6ff;--s1:#ffa657;--s2:#3fb950;--s3:#d2a8ff;
--s4:#ff7b72;--s5:#8b949e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:1rem 1.25rem 3rem}
a.skip{position:absolute;left:-999px}a.skip:focus{left:1rem;top:1rem;background:var(--card);padding:.5rem}
h1{font-size:1.6rem;margin:.5rem 0}h2{font-size:1.25rem;margin:2rem 0 .5rem;border-bottom:1px solid var(--line)}
h3{font-size:1.05rem;margin:1.25rem 0 .25rem}p,li{max-width:70ch}.muted{color:var(--muted)}
.banner{border:1px solid var(--line);background:var(--card);padding:.5rem .75rem;border-radius:6px}
table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.92rem;display:block;overflow-x:auto}
caption{text-align:left;color:var(--muted);padding:.25rem 0}
th,td{border:1px solid var(--line);padding:.3rem .5rem;text-align:left;vertical-align:top}
th{background:var(--card)}td.n{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1rem}
figure{margin:0;border:1px solid var(--line);border-radius:6px;padding:.5rem;background:var(--card)}
figcaption{font-size:.85rem;color:var(--muted)}svg{width:100%;height:auto;display:block}
svg text{fill:var(--fg);font:11px system-ui,sans-serif}svg .ax{stroke:var(--muted);stroke-width:1}
svg .gr{stroke:var(--line);stroke-width:1}svg .box{fill:var(--card);stroke:var(--muted);stroke-width:1.2}
svg .arr{stroke:var(--muted);stroke-width:1.5;fill:none}svg .ah{fill:var(--muted)}
.s0{color:var(--s0)}.s1{color:var(--s1)}.s2{color:var(--s2)}.s3{color:var(--s3)}.s4{color:var(--s4)}.s5{color:var(--s5)}
pre{background:var(--card);border:1px solid var(--line);padding:.75rem;overflow:auto;font-size:.82rem}
code{font-family:ui-monospace,Consolas,monospace}
@media print{body{background:#fff;color:#000}}
"""


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    span = max(hi - lo, 1e-9)
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    out, t = [], math.floor(lo / step) * step
    while t <= hi + 1e-9:
        if t >= lo - 1e-9:
            out.append(round(t, 9))
        t += step
    return out


def _marker(shape: int, x: float, y: float, filled: bool) -> str:
    r = 3.0
    paint = (
        'fill="currentColor"' if filled else 'fill="none" stroke="currentColor" stroke-width="1.3"'
    )
    if shape == 0:
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" {paint}/>'
    if shape == 1:
        return f'<rect x="{x - r:.1f}" y="{y - r:.1f}" width="{2 * r}" height="{2 * r}" {paint}/>'
    if shape == 2:
        pts = f"{x:.1f},{y - r - 1:.1f} {x - r - 1:.1f},{y + r:.1f} {x + r + 1:.1f},{y + r:.1f}"
        return f'<polygon points="{pts}" {paint}/>'
    pts = f"{x:.1f},{y - r - 1:.1f} {x + r + 1:.1f},{y:.1f} {x:.1f},{y + r + 1:.1f} {x - r - 1:.1f},{y:.1f}"
    return f'<polygon points="{pts}" {paint}/>'


def _age_chart(
    ident: str, name: str, index: int, points: list[tuple[float, float, bool]], end: float
) -> str:
    """Scatter of data age at delivery against delivery time, for one subsystem."""
    w, h, left, right, top, bottom = 340, 210, 46, 10, 12, 34
    if len(points) > 300:  # keep the file small: an even subsample is plenty to see the shape
        step = len(points) / 300
        points = [points[int(i * step)] for i in range(300)]
    ymax = max((p[1] for p in points), default=1.0) * 1.1 or 1.0

    def px(t: float) -> float:
        return left + (w - left - right) * t / end

    def py(a: float) -> float:
        return h - bottom - (h - top - bottom) * a / ymax

    shape = index % 4
    n_est = sum(1 for p in points if p[2])
    desc = (
        f"Scatter plot, {len(points)} points shown. Horizontal axis: delivery time in seconds, 0 to {end:g}. "
        f"Vertical axis: age of the data when it was delivered, in milliseconds, 0 to {ymax:.0f}. "
        f"{'Hollow markers are estimates (' + str(n_est) + ' shown). ' if n_est else ''}"
        + (
            f"Largest age shown: {max(p[1] for p in points):.1f} ms."
            if points
            else "No data was delivered."
        )
    )
    parts = [
        f'<svg role="img" aria-labelledby="{ident}-t {ident}-d" viewBox="0 0 {w} {h}">',
        f'<title id="{ident}-t">{E(name)}: age of each datum at delivery</title>',
        f'<desc id="{ident}-d">{E(desc)}</desc>',
    ]
    for v in _ticks(0, ymax):
        parts.append(
            f'<line class="gr" x1="{left}" x2="{w - right}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
        )
        parts.append(f'<text x="{left - 5}" y="{py(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    for v in _ticks(0, end):
        parts.append(
            f'<text x="{px(v):.1f}" y="{h - bottom + 14}" text-anchor="middle">{v:g}</text>'
        )
    parts += [
        f'<line class="ax" x1="{left}" x2="{left}" y1="{top}" y2="{h - bottom}"/>',
        f'<line class="ax" x1="{left}" x2="{w - right}" y1="{h - bottom}" y2="{h - bottom}"/>',
        f'<text x="{(left + w - right) / 2:.0f}" y="{h - 4}" text-anchor="middle">delivery time (s)</text>',
        f'<text transform="translate(11 {(top + h - bottom) / 2:.0f}) rotate(-90)" text-anchor="middle">age (ms)</text>',
        f'<g class="s{index % 6}">',
    ]
    parts += [_marker(shape, px(t), py(a), not est) for t, a, est in points]
    parts += ["</g></svg>"]
    return "".join(parts)


def _architecture_svg(d: dict[str, Any]) -> str:
    msgs = [m["message"] for m in d["baseline"]["published"]]
    subs = d["subsystems"]
    rows = max(len(msgs), len(subs))
    h = 36 + 30 * rows + 60
    parts = [
        f'<svg role="img" aria-labelledby="arch-t arch-d" viewBox="0 0 780 {h}">',
        '<title id="arch-t">Architecture of the example</title>',
        f'<desc id="arch-d">The baseline publishes {len(msgs)} messages ({E(", ".join(msgs))}) to the Bridge. '
        f"The Bridge contains the OEM Interface Unit, the Translator Module and the Unique Interface "
        f"Distributor, with a health monitor and a cyber monitor. It connects to {len(subs)} unique "
        f"systems: {E(', '.join(s['name'] + ' (' + s['protocol'] + ')' for s in subs))}.</desc>",
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto"><path class="ah" d="M0 0L10 5L0 10z"/></marker></defs>',
        f'<rect class="box" x="8" y="8" width="210" height="{h - 16}" rx="8"/>',
        '<text x="113" y="26" text-anchor="middle" font-weight="600">Baseline Island</text>',
    ]
    for i, m in enumerate(msgs):
        parts.append(f'<text x="22" y="{52 + 30 * i}">{E(m)}</text>')
    parts.append(f'<rect class="box" x="290" y="8" width="200" height="{h - 16}" rx="8"/>')
    parts.append('<text x="390" y="26" text-anchor="middle" font-weight="600">Bridge</text>')
    labels = ["OIU (remote terminal)", "Translator Module", "UID (controlling side)"]
    for i, label in enumerate(labels):
        y = 44 + i * 52
        parts.append(f'<rect class="box" x="306" y="{y}" width="168" height="36" rx="5"/>')
        parts.append(f'<text x="390" y="{y + 22}" text-anchor="middle">{label}</text>')
        if i:
            parts.append(f'<path class="arr" d="M390 {y - 16}V{y}" marker-end="url(#ah)"/>')
    parts.append(
        f'<text x="390" y="{44 + 3 * 52 + 14}" text-anchor="middle" class="muted">health monitor, cyber monitor</text>'
    )
    mid = h // 2
    parts.append(f'<path class="arr" d="M218 {mid}H288" marker-end="url(#ah)"/>')
    parts.append(f'<rect class="box" x="560" y="8" width="212" height="{h - 16}" rx="8"/>')
    parts.append('<text x="666" y="26" text-anchor="middle" font-weight="600">Unique Island</text>')
    for i, s in enumerate(subs):
        y = 52 + 30 * i
        parts.append(
            f'<text x="572" y="{y}">{E(s["name"])} <tspan class="muted">{E(s["protocol"])}</tspan></text>'
        )
    parts.append(f'<path class="arr" d="M490 {mid}H558" marker-end="url(#ah)"/>')
    parts.append("</svg>")
    return "".join(parts)


def _table(
    caption: str,
    head: list[str],
    rows: list[list[Any]],
    numeric: set[int] | None = None,
    raw: set[int] | None = None,
) -> str:
    numeric, raw = numeric or set(), raw or set()
    out = [f"<table><caption>{E(caption)}</caption><thead><tr>"]
    out += [f'<th scope="col">{E(h)}</th>' for h in head]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for i, cell in enumerate(row):
            text = str(cell) if i in raw else E(str(cell))
            out.append(f"<td{' class=n' if i in numeric else ''}>{text}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


table = _table  # shared with the local app


def age_charts(
    prefix: str, subsystems: list[str], trace: list[dict[str, Any]], duration_s: float
) -> str:
    """A grid of age-at-delivery charts, one per subsystem, as inline SVG."""
    parts = ['<div class="grid">']
    for idx, name in enumerate(subsystems):
        pts = [
            (r["delivered_at"], 1000 * r["age_s"], bool(r["estimated"]))
            for r in trace
            if r["subsystem"] == name
        ]
        chart = _age_chart(f"{prefix}-c{idx}", name, idx, pts, duration_s)
        parts.append(f"<figure>{chart}<figcaption>{E(name)}</figcaption></figure>")
    parts.append("</div>")
    return "".join(parts)


def build_report(
    world: ModuleType,
    seed: int = 1,
    rate_policy: str = "cap",
    duration_s: float = 6.0,
    stall: tuple[float, float] | None = None,
) -> str:
    versions = list(world.VERSIONS)
    body: list[str] = []
    first, last = versions[0], versions[-1]
    arch = describe(world, first)

    body.append(f"<h1>bridge-island report: {E(world.NAME)}</h1>")
    stall_note = f"; Bridge stalled at {stall[0]:g} s for {stall[1]:g} s" if stall else ""
    body.append(
        f'<p class="banner">{SYNTHETIC} Run: seed {seed}, {duration_s:g} s simulated, rate policy '
        f"<code>{E(rate_policy)}</code>{E(stall_note)}.</p>"
    )
    body.append('<h2 id="architecture">Architecture</h2>')
    body.append(
        f"<figure>{_architecture_svg(arch)}<figcaption>Every exchange between the islands goes through the Bridge.</figcaption></figure>"
    )

    runs: dict[int, Any] = {}
    for v in versions:
        opts: dict[str, Any] = {"rate_policy": rate_policy}
        system = world.build(v, seed, **opts)
        assert system.bridge is not None
        if stall:
            system.bridge.stall(*stall)
        system.run(duration_s)
        runs[v] = (
            system,
            summarize(system),
            trace_rows(system),
            describe(world, v),
            check_rules(world, v),
        )

    body.append('<h2 id="rules">Design guidelines G1 to G6</h2>')
    rule_rows = []
    for rule_index in range(6):
        row = [runs[first][4][rule_index].rule, runs[first][4][rule_index].title]
        for v in versions:
            r = runs[v][4][rule_index]
            row.append(
                f'<span class="{"ok" if r.passed else "bad"}">{"&#10003; pass" if r.passed else "&#10007; FAIL"}</span> {E(r.detail)}'
            )
        rule_rows.append(row)
    body.append(
        _table(
            "Each guideline, checked on every baseline version",
            ["Rule", "Guideline", *[f"Baseline v{v}" for v in versions]],
            rule_rows,
            raw=set(range(2, 2 + len(versions))),
        )
    )

    for v in versions:
        system, summary, trace, desc, _ = runs[v]
        c = desc["counts"]
        body.append(f'<h2 id="v{v}">Baseline interface v{v}</h2>')
        body.append(
            f"<p>{c['published_elements']} published elements, {c['subsystems']} unique systems, {c['paths']} paths: {c['identity']} pass through unchanged, {c['converted']} converted, {c['removed']} removed by the baseline.</p>"
        )
        rows = []
        for name, s in summary["subsystems"].items():
            issues = "; ".join(s["issues"]) or "none"
            rows.append(
                [
                    name,
                    s["protocol"],
                    s["received"],
                    s["estimated"],
                    _fmt(s["mean_age_ms"]),
                    _fmt(s["max_age_ms"]),
                    s["late"],
                    issues,
                ]
            )
        body.append(
            _table(
                f"What each unique system received in {duration_s:g} s",
                [
                    "System",
                    "Native bus",
                    "Received",
                    "Estimated",
                    "Mean age (ms)",
                    "Max age (ms)",
                    "Late",
                    "Issues",
                ],
                rows,
                numeric={2, 3, 4, 5, 6},
            )
        )
        body.append("<h3>Age of the data at delivery</h3>")
        body.append(age_charts(f"v{v}", list(summary["subsystems"]), trace, duration_s))
        path_rows = []
        for p in desc["paths"]:
            how = {"identity": "identity (unchanged)", "removed": f"removed: {p['reason']}"}.get(
                p["kind"], ", ".join(p["transforms"])
            )
            path_rows.append([p["from"], p["to"], p["kind"], how])
        body.append(
            _table(
                f"Mapping for baseline v{v}: every path from a baseline element to a unique-system input",
                ["Baseline element", "Unique-system input", "Kind", "How"],
                path_rows,
            )
        )
        bridge = summary["bridge"]
        body.append(
            f"<p>Bridge counters: {bridge['identity_paths_used']} identity pass-throughs, {bridge['translated_paths_used']} translated, "
            f"{bridge['decimated']} samples dropped to fit subsystem rates, {bridge['estimates_delivered']} estimates delivered, "
            f"{bridge['blocked_by_cyber_monitor']} blocked by the cyber monitor. Links that skip the Bridge: {summary['network']['bypass_links']}.</p>"
        )
        events = bridge["events"]
        if events:
            ev_rows = [
                [f"{e['time_s']:g}", e["source"], e["kind"], e["detail"]] for e in events[:40]
            ]
            more = f" (first 40 of {len(events)})" if len(events) > 40 else ""
            body.append(
                _table(
                    f"Bridge events{more}",
                    ["Time (s)", "Source", "Kind", "Detail"],
                    ev_rows,
                    numeric={0},
                )
            )

    if len(versions) > 1:
        d = diff_versions(world, first, last)
        body.append(f'<h2 id="change">What the change from v{first} to v{last} touches</h2>')
        body.append(
            _table(
                "Interface changes",
                ["Kind", "Element"],
                [[c["kind"], c["ref"]] for c in d["changes"]],
            )
        )
        spec = d["mapping_spec"]
        body.append(
            f"<p>On the Bridge the change lands in one file, measured on the real files: <code>{E(spec['file'])}</code> changes by +{spec['added']} / &minus;{spec['removed']} lines. Unique systems that must also handle a removed element: {E(', '.join(d['unique_subsystems_that_must_handle_a_removal']) or 'none')}.</p>"
        )
        p, b = d["modelled_impact"]["p2p"], d["modelled_impact"]["bridge"]
        labels = [
            ("subsystems_code_change", "Unique systems needing code change"),
            ("modules_touched", "Modules touched"),
            ("tests_failing", "Tests failing before the fix"),
            ("components_reverified", "Components needing re-verification"),
        ]
        body.append(
            _table(
                "Modelled impact (counting model; the tests row depends on an arbitrary assumption, see the documentation)",
                ["", "Point to point", "Bridge"],
                [[label, p[k], b[k]] for k, label in labels],
                numeric={1, 2},
            )
        )
        body.append(
            '<p class="muted">The Bridge concentrates the change in one mapping. It does not make it disappear.</p>'
        )

    scenario = Scenario(
        name="report",
        example=world.NAME,
        duration_s=duration_s,
        seed=seed,
        bridge={"rate_policy": rate_policy},
        events=[{"at": stall[0], "stall": stall[1]}] if stall else [],
    )
    man = manifest(scenario, world, last)
    body.append('<h2 id="repro">Reproduce this report</h2>')
    command = f"bridge-island report {world.NAME} --seed {seed} --duration {duration_s:g}"
    command += f" --rate-policy {rate_policy}"
    if stall:
        command += f" --stall {stall[0]:g},{stall[1]:g}"
    body.append(
        f"<p>Run <code>{E(command)}</code>. With the same package and Python versions the "
        "result is the same file.</p>"
    )
    body.append(f"<pre>{E(json.dumps(man, indent=2))}</pre>")

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="generator" content="bridge-island {bridge_island.__version__}">'
        f"<title>bridge-island report: {E(world.NAME)}</title><style>{CSS}</style></head><body>"
        '<a class="skip" href="#main">Skip to content</a><main id="main">'
        + "".join(body)
        + "</main></body></html>"
    )
