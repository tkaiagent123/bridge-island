"""Inline SVG charts for the insulin loop (no plotting library: they also run in the browser app).

Colours come from CSS variables (``--arch-direct`` and so on), set in :data:`CSS` for light and dark
themes, so the app, the report and the matplotlib figures share one palette. Each chart has a
title and a text description, and the architectures differ by line style as well as colour.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from bridge_island.clinical.figures import ARCH_COLORS

E = html.escape
ARCH_DASH = {"direct": "", "bridge-basic": "6 3", "bridge-contract": "2 2"}
CSS = (
    ":root{"
    + "".join(f"--arch-{a}:{c};" for a, c in ARCH_COLORS.items())
    + "--band:rgba(0,158,115,.10);--shade:rgba(120,120,120,.22)}"
    "@media (prefers-color-scheme:dark){:root{--arch-direct:#F0884D;--arch-bridge-basic:#56B4E9;"
    "--arch-bridge-contract:#3CC59A;--band:rgba(60,197,154,.14);--shade:rgba(200,200,200,.18)}}"
    ".lg{display:inline-flex;align-items:center;gap:.35rem;margin-right:1rem;font-size:.85rem}"
    ".lg i{display:inline-block;width:1.6rem;height:0;border-top:3px solid}"
)


def legend(archs: Sequence[str]) -> str:
    return "".join(
        f'<span class="lg"><i style="border-color:var(--arch-{a});border-top-style:'
        f'{"solid" if not ARCH_DASH[a] else ("dashed" if a == "bridge-basic" else "dotted")}"></i>{E(a)}</span>'
        for a in archs
    )


def glucose_chart(
    ident: str,
    traces: dict[str, Sequence[float]],
    events: Sequence[tuple[float, str]] = (),
    shaded: Sequence[tuple[float, float]] = (),
    title: str = "Glucose",
) -> str:
    """Plasma glucose (one value a minute) per architecture over 24 h, with the 70-180 band, the 70
    and 54 lines, fault times and fail-safe periods (shaded)."""
    w, h, left, right, top, bottom = 720, 260, 46, 10, 10, 34
    hours = max((len(t) for t in traces.values()), default=1441) / 60
    ymax = max(300.0, max((max(t) for t in traces.values() if t), default=300) * 1.05)

    def px(minute: float) -> float:
        return left + (w - left - right) * minute / 60 / hours

    def py(g: float) -> float:
        return h - bottom - (h - top - bottom) * g / ymax

    peak = {a: max(t) for a, t in traces.items() if t}
    low = {a: min(t) for a, t in traces.items() if t}
    desc = (
        f"Line chart of glucose in mg/dL over {hours:.0f} hours for {', '.join(traces)}. "
        + " ".join(f"{a}: lowest {low[a]:.0f}, highest {peak[a]:.0f}." for a in traces)
        + (
            f" Fault times: {', '.join(f'{t / 3600:.1f} h ({lab})' for t, lab in events)}."
            if events
            else ""
        )
        + (f" Shaded: {len(shaded)} fail-safe period(s)." if shaded else "")
        + " Synthetic, illustrative."
    )
    out = [
        f'<svg role="img" aria-labelledby="{ident}-t {ident}-d" viewBox="0 0 {w} {h}">',
        f'<title id="{ident}-t">{E(title)} (synthetic, illustrative)</title><desc id="{ident}-d">{E(desc)}</desc>',
        f'<rect x="{left}" y="{py(180):.1f}" width="{w - left - right}" height="{py(70) - py(180):.1f}" fill="var(--band)"/>',
    ]
    for a, b in shaded:
        out.append(
            f'<rect x="{px(a / 60):.1f}" y="{top}" width="{max(1.0, px(b / 60) - px(a / 60)):.1f}" '
            f'height="{h - top - bottom}" fill="var(--shade)"><title>fail-safe</title></rect>'
        )
    for g, dash in ((70, "5 3"), (54, "2 2")):
        out.append(
            f'<line class="ax" x1="{left}" x2="{w - right}" y1="{py(g):.1f}" y2="{py(g):.1f}" stroke-dasharray="{dash}"/>'
            f'<text x="{w - right - 2}" y="{py(g) - 3:.1f}" text-anchor="end">{g}</text>'
        )
    for g in range(0, int(ymax) + 1, 100):
        out.append(f'<text x="{left - 4}" y="{py(g) + 4:.1f}" text-anchor="end">{g}</text>')
    for hr in range(0, int(hours) + 1, 4):
        out.append(
            f'<text x="{px(hr * 60):.1f}" y="{h - bottom + 14}" text-anchor="middle">{hr:02d}:00</text>'
        )
    out.append(
        f'<text x="{(w + left) / 2}" y="{h - 4}" text-anchor="middle">time of day (h)</text>'
        f'<text x="12" y="{(h - bottom) / 2}" transform="rotate(-90 12 {(h - bottom) / 2})" text-anchor="middle">glucose (mg/dL)</text>'
    )
    for t, label in events:
        out.append(
            f'<line x1="{px(t / 60):.1f}" x2="{px(t / 60):.1f}" y1="{top}" y2="{h - bottom}" stroke="currentColor" stroke-width="1"/>'
            f'<text x="{px(t / 60) + 3:.1f}" y="{top + 10}">{E(label)}</text>'
        )
    for arch, trace in traces.items():
        step = max(1, len(trace) // 360)
        pts = " ".join(f"{px(i):.1f},{py(g):.1f}" for i, g in enumerate(trace) if i % step == 0)
        dash = f' stroke-dasharray="{ARCH_DASH[arch]}"' if ARCH_DASH[arch] else ""
        out.append(
            f'<polyline fill="none" stroke="var(--arch-{arch})" stroke-width="1.6"{dash} points="{pts}"/>'
        )
    out.append(f'<line class="ax" x1="{left}" x2="{left}" y1="{top}" y2="{h - bottom}"/></svg>')
    return "".join(out)


def insulin_strip(
    ident: str, deliveries: dict[str, Sequence[tuple[float, float]]], hours: float = 24.0
) -> str:
    """Boluses delivered (U) per architecture, one row each."""
    w, row, left = 720, 26, 110
    h = row * len(deliveries) + 24
    total = {a: sum(u for _, u in d) for a, d in deliveries.items()}
    desc = "Bolus insulin delivered over the day. " + " ".join(
        f"{a}: {len(d)} boluses, {total[a]:.1f} U." for a, d in deliveries.items()
    )
    biggest = max((u for d in deliveries.values() for _, u in d), default=1.0) or 1.0
    out = [
        f'<svg role="img" aria-labelledby="{ident}-t {ident}-d" viewBox="0 0 {w} {h}">',
        f'<title id="{ident}-t">Insulin delivered (synthetic, illustrative)</title><desc id="{ident}-d">{E(desc)}</desc>',
    ]
    for i, (arch, ds) in enumerate(deliveries.items()):
        base = row * (i + 1)
        out.append(f'<text x="4" y="{base - 6}">{E(arch)} ({total[arch]:.1f} U)</text>')
        out.append(f'<line class="ax" x1="{left}" x2="{w - 10}" y1="{base}" y2="{base}"/>')
        for t, u in ds:
            x = left + (w - left - 10) * t / 3600 / hours
            out.append(
                f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{base}" y2="{base - 18 * u / biggest:.1f}" '
                f'stroke="var(--arch-{arch})" stroke-width="3"><title>{u:.2f} U at {t / 3600:.2f} h</title></line>'
            )
    out.append(
        f'<text x="{left}" y="{h - 4}">units of bolus per delivery; across: 00:00 to {hours:.0f}:00</text></svg>'
    )
    return "".join(out)
