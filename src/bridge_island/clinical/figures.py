"""Publication figures for the insulin loop (matplotlib, optional ``plots`` extra).

Every figure has a title, labelled axes with units, a legend and the words "synthetic,
illustrative". The three architectures use the same three colours everywhere (figures, app and
report): a colour-blind-safe set from the Okabe-Ito palette. PNG at 300 dpi and SVG, with fixed
metadata so the same seed gives the same files.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bridge_island.clinical import loop as L

LABEL = "synthetic, illustrative"
ARCH_COLORS = {"direct": "#D55E00", "bridge-basic": "#0072B2", "bridge-contract": "#009E73"}
ARCH_MARKERS = {"direct": "o", "bridge-basic": "s", "bridge-contract": "D"}
SCENARIO_ORDER = (
    "nominal",
    "sensor_swap_units",
    "sensor_dropout",
    "stale_burst",
    "pump_fw_v2",
    "pump_silent",
    "out_of_range",
    "replayed_command",
    "display_throttle",
    "bridge_stall",
)


def _plt() -> Any | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    plt.rcParams.update(
        {"svg.hashsalt": "bridge-island", "font.size": 8, "axes.titlesize": 9, "legend.fontsize": 7}
    )
    return plt


def _save(fig: Any, out: Path, stem: str) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    png, svg = out / f"{stem}.png", out / f"{stem}.svg"
    fig.savefig(png, dpi=300, metadata={"Software": None}, bbox_inches="tight")
    fig.savefig(svg, metadata={"Date": None, "Creator": None}, bbox_inches="tight")
    fig.clf()
    import matplotlib.pyplot as plt

    plt.close(fig)
    return {f"{stem}_png": png, f"{stem}_svg": svg}


# ---- Fig 1: the architecture (hand-made SVG, no matplotlib needed) ----------------------------


def architecture_svg() -> str:
    """Islands, Bridge parts, the contract monitor and the environment, at single-column width."""
    box = '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{f}" stroke="#444" stroke-width="1"/>'
    txt = '<text x="{x}" y="{y}" text-anchor="middle" font-size="{s}" font-family="sans-serif" fill="#111">{t}</text>'
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 340 300" width="340" height="300" role="img" '
        'aria-labelledby="t d"><title id="t">Generic Glycemic Loop architecture (synthetic, illustrative)</title>'
        '<desc id="d">The controller host (Baseline Island) connects only to the Bridge. The Bridge holds the OIU, '
        "Translator Module, UID, health and cyber monitors and the contract monitor. The Unique Island holds "
        "SensorA, SensorB, PumpA, PumpB v2 and the CompanionDisplay. The patient model, outside both islands, "
        "is sampled by the sensors and driven by the pumps.</desc>",
        '<rect width="340" height="300" fill="#ffffff"/>',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#444"/></marker></defs>',
        box.format(x=10, y=10, w=320, h=40, f="#E8E8E8"),
        txt.format(x=170, y=27, s=10, t="Baseline Island: Controller Host (immutable)"),
        txt.format(
            x=170,
            y=41,
            s=8,
            t="accepts GlucoseReading, DeliveryConfirmation, AlarmAck; publishes DoseCommand, ControllerStatus",
        ),
        box.format(x=10, y=70, w=320, h=86, f="#DCEBF5"),
        txt.format(x=170, y=86, s=10, t="Bridge"),
    ]
    for i, name in enumerate(("OIU", "Translator", "UID")):
        parts += [
            box.format(x=22 + i * 104, y=94, w=88, h=22, f="#ffffff"),
            txt.format(x=66 + i * 104, y=109, s=8, t=name),
        ]
    for i, name in enumerate(("health", "cyber", "contract monitor")):
        fill = "#CDEBDD" if name.startswith("contract") else "#ffffff"
        parts += [
            box.format(x=22 + i * 104, y=124, w=88, h=22, f=fill),
            txt.format(x=66 + i * 104, y=139, s=8, t=name),
        ]
    parts += [
        box.format(x=10, y=176, w=320, h=56, f="#F6E8DA"),
        txt.format(x=170, y=191, s=10, t="Unique Island (independently owned)"),
    ]
    for i, name in enumerate(("SensorA", "SensorB", "PumpA", "PumpB v2", "Display")):
        parts += [
            box.format(x=16 + i * 63, y=200, w=58, h=22, f="#ffffff"),
            txt.format(x=45 + i * 63, y=215, s=7.5, t=name),
        ]
    parts += [
        box.format(x=60, y=252, w=220, h=36, f="#F2F2F2"),
        txt.format(x=170, y=268, s=9, t="Environment: invented patient model"),
        txt.format(x=170, y=281, s=7.5, t="sampled by the sensors, driven by the pumps"),
        '<line x1="170" y1="50" x2="170" y2="68" stroke="#444" marker-end="url(#a)"/>',
        '<line x1="160" y1="68" x2="160" y2="52" stroke="#444" marker-end="url(#a)"/>',
        '<line x1="170" y1="156" x2="170" y2="174" stroke="#444" marker-end="url(#a)"/>',
        '<line x1="160" y1="174" x2="160" y2="158" stroke="#444" marker-end="url(#a)"/>',
        '<line x1="76" y1="250" x2="76" y2="224" stroke="#444" stroke-dasharray="3 2" marker-end="url(#a)"/>',
        '<line x1="234" y1="224" x2="234" y2="250" stroke="#444" stroke-dasharray="3 2" marker-end="url(#a)"/>',
        txt.format(x=300, y=296, s=7, t=LABEL),
        "</svg>",
    ]
    return "\n".join(parts) + "\n"


def write_architecture(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "fig1_architecture.svg"
    path.write_text(architecture_svg(), encoding="utf-8", newline="\n")
    return {"fig1_svg": path}


# ---- Fig 2: glucose traces under sensor_swap_units ----------------------------------------------


def _shade(ax: Any, spans: Sequence[tuple[float, float]], label: str) -> None:
    for i, (a, b) in enumerate(spans):
        ax.axvspan(
            a / 3600, b / 3600, color="#999999", alpha=0.25, lw=0, label=label if i == 0 else None
        )


def failsafe_spans(loop: L.Loop) -> list[tuple[float, float]]:
    if loop.bridge is None:
        return []
    spans, since = [], None
    for e in loop.bridge.log.events:
        if e.kind == "contract.failsafe" and since is None:
            since = e.time
        elif e.kind == "contract.recovered" and since is not None:
            spans.append((since, e.time))
            since = None
    if since is not None:
        spans.append((since, loop.scenario.duration_s))
    return spans


def traces(
    plt: Any, out: Path, scenario: str, patient: int, seed: int, stem: str
) -> dict[str, Path]:
    from bridge_island.clinical import scenarios as S

    sc = S.by_name(scenario)
    loops = {}
    for arch in L.ARCHS:
        lp = L.build_loop(arch, L.patients(seed)[patient], sc)
        lp.run()
        loops[arch] = lp
    fig, axes = plt.subplots(3, 1, figsize=(3.4, 5.2), sharex=True, sharey=True)
    event = L.fault_windows(sc, loops["direct"].meals)
    for ax, (arch, lp) in zip(axes, loops.items(), strict=True):
        hours = [i / 60 for i in range(len(lp.patient.trace))]
        ax.axhspan(70, 180, color="#009E73", alpha=0.08, lw=0, label="70-180 mg/dL")
        ax.axhline(70, color="#666", lw=0.6, ls="--", label="70 mg/dL")
        ax.axhline(54, color="#666", lw=0.6, ls=":", label="54 mg/dL")
        _shade(ax, failsafe_spans(lp), "fail-safe")
        ax.plot(hours, lp.patient.trace, color=ARCH_COLORS[arch], lw=1.0, label=f"{arch} glucose")
        for _, start, _ in event:
            ax.axvline(start / 3600, color="#111", lw=0.8, label="fault starts")
        ax.set_ylabel("glucose (mg/dL)")
        ax.set_title(arch, loc="left")
    axes[-1].set_xlabel("time of day (h)")
    axes[-1].set_xlim(0, 24)
    handles, labels = [], []
    for ax in axes:
        for h, lab in zip(*ax.get_legend_handles_labels(), strict=True):
            if lab not in labels:
                handles.append(h)
                labels.append(lab)
    fig.legend(
        handles, labels, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.09)
    )
    fig.suptitle(f"{scenario}, {L.patients(seed)[patient].id} ({LABEL})")
    return _save(fig, out, stem)


# ---- Figs 3, 4, 6 from the run rows -------------------------------------------------------------


def _ordered(rows: Sequence[dict[str, Any]]) -> list[str]:
    present = {r["scenario"] for r in rows}
    return [s for s in SCENARIO_ORDER if s in present] + sorted(present - set(SCENARIO_ORDER))


def detection(plt: Any, out: Path, rows: Sequence[dict[str, Any]]) -> dict[str, Path]:
    scen = [s for s in _ordered(rows) if s != "nominal"]
    fig, ax = plt.subplots(figsize=(3.4, 3.8))
    for j, arch in enumerate(L.ARCHS):
        for i, s in enumerate(scen):
            group = [r for r in rows if r["scenario"] == s and r["architecture"] == arch]
            times = sorted(r["time_to_detect_s"] / 60 for r in group if r["detected"])
            y = i + (j - 1) * 0.25
            if not times:
                absorbed = s in ("sensor_swap_units", "pump_fw_v2") and arch != "direct"
                text = "absorbed by the mapping: nothing to detect" if absorbed else "not detected"
                ax.text(0.2, y, text, color=ARCH_COLORS[arch], fontsize=6, va="center")
                continue
            lo, mid, hi = (
                times[int(0.05 * len(times))],
                times[len(times) // 2],
                times[min(len(times) - 1, int(0.95 * len(times)))],
            )
            ax.plot([lo, hi], [y, y], color=ARCH_COLORS[arch], lw=1)
            ax.plot(
                mid,
                y,
                ARCH_MARKERS[arch],
                color=ARCH_COLORS[arch],
                ms=4,
                label=arch if i == 0 else None,
            )
            if len(times) < len(group):
                ax.text(
                    hi + 0.5,
                    y,
                    f"{len(times)}/{len(group)}",
                    fontsize=5.5,
                    va="center",
                    color=ARCH_COLORS[arch],
                )
    ax.set_yticks(range(len(scen)), scen)
    ax.invert_yaxis()
    ax.set_xlabel("time to tell the controller or display (min); median, 5th-95th pct")
    ax.set_title(f"Time to detect, 30 virtual patients ({LABEL})")
    handles = [
        plt.Line2D([], [], marker=ARCH_MARKERS[a], color=ARCH_COLORS[a], ls="", label=a)
        for a in L.ARCHS
    ]
    ax.legend(
        handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False
    )
    return _save(fig, out, "fig3_time_to_detect")


def harms(plt: Any, out: Path, rows: Sequence[dict[str, Any]]) -> dict[str, Path]:
    scen = _ordered(rows)
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 3.6), sharey=True)
    for ax, key, title in (
        (axes[0], "unsafe_age_min", "unflagged stale glucose used (min)"),
        (axes[1], "below_54_min", "time below 54 mg/dL (min)"),
    ):
        for j, arch in enumerate(L.ARCHS):
            for i, s in enumerate(scen):
                vals = sorted(
                    r[key] for r in rows if r["scenario"] == s and r["architecture"] == arch
                )
                if not vals:
                    continue
                lo, mid, hi = (
                    vals[int(0.05 * len(vals))],
                    vals[len(vals) // 2],
                    vals[min(len(vals) - 1, int(0.95 * len(vals)))],
                )
                y = i + (j - 1) * 0.25
                ax.plot([lo, hi], [y, y], color=ARCH_COLORS[arch], lw=1)
                ax.plot(
                    mid,
                    y,
                    ARCH_MARKERS[arch],
                    color=ARCH_COLORS[arch],
                    ms=3.5,
                    label=arch if i == 0 else None,
                )
        ax.set_title(title)
        ax.set_xlabel("minutes per patient-day; median, 5th-95th pct")
    axes[0].set_yticks(range(len(scen)), scen)
    axes[0].invert_yaxis()
    axes[1].legend(loc="upper center", bbox_to_anchor=(-0.1, -0.14), ncol=3, frameon=False)
    fig.suptitle(f"Interface harm per scenario, 30 virtual patients ({LABEL})")
    return _save(fig, out, "fig4_unsafe_age_and_lows")


def costs(plt: Any, out: Path, rows: Sequence[dict[str, Any]]) -> dict[str, Path]:
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 3.0))
    nominal = [r for r in rows if r["scenario"] == "nominal"]
    for j, arch in enumerate(L.ARCHS):
        lat = [
            r["host_to_pump_median_s"]
            for r in nominal
            if r["architecture"] == arch and r["host_to_pump_median_s"] is not None
        ]
        sen = [
            r["sensor_to_host_median_s"]
            for r in nominal
            if r["architecture"] == arch and r["sensor_to_host_median_s"] is not None
        ]
        axes[0].scatter(
            [j - 0.12] * len(lat),
            lat,
            color=ARCH_COLORS[arch],
            marker=ARCH_MARKERS[arch],
            s=9,
            label=f"{arch}: controller to pump",
        )
        axes[0].scatter(
            [j + 0.12] * len(sen),
            sen,
            facecolors="none",
            edgecolors=ARCH_COLORS[arch],
            marker=ARCH_MARKERS[arch],
            s=9,
            label=f"{arch}: sensor to controller",
        )
    axes[0].set_xticks(range(3), L.ARCHS)
    axes[0].set_ylabel("median latency per patient (s)")
    axes[0].set_title("Added latency, nominal day")
    axes[0].legend(frameon=False, fontsize=5.5)
    stall = [r for r in rows if r["scenario"] == "bridge_stall"]
    keys = (
        ("paths_failed_in_stall", "device paths\nthat carried nothing"),
        ("unsafe_age_min", "unflagged stale\nglucose used (min)"),
    )
    for j, arch in enumerate(L.ARCHS):
        group = [r for r in stall if r["architecture"] == arch]
        for k, (key, _) in enumerate(keys):
            vals = sorted(r[key] or 0 for r in group)
            if vals:
                axes[1].bar(
                    k + (j - 1) * 0.27,
                    vals[len(vals) // 2],
                    0.25,
                    color=ARCH_COLORS[arch],
                    label=arch if k == 0 else None,
                )
    axes[1].set_xticks(range(2), [label for _, label in keys])
    axes[1].set_ylabel("median over the cohort (count, min)")
    axes[1].text(
        -0.27, 0.3, "direct: 0\n(no Bridge)", color=ARCH_COLORS["direct"], ha="center", fontsize=6
    )
    axes[1].set_title("bridge_stall: 20-minute Bridge stall")
    axes[1].legend(frameon=False)
    fig.suptitle(f"What the Bridge costs ({LABEL})", y=1.04)
    return _save(fig, out, "fig6_bridge_costs")


def pick_patient(rows: Sequence[dict[str, Any]], scenario: str) -> int:
    """The virtual patient with the median difference in time in range, bridge-contract minus
    direct (a typical patient, not the most dramatic one)."""
    by: dict[str, dict[str, int]] = {}
    for r in rows:
        if r["scenario"] == scenario:
            by.setdefault(r["patient"], {})[r["architecture"]] = r["tir_70_180_min"]
    ranked = sorted(by, key=lambda p: (by[p]["bridge-contract"] - by[p]["direct"], p))
    return int(ranked[len(ranked) // 2][2:]) - 1


def closed_loop_figures(
    out: Path, rows: Sequence[dict[str, Any]], summary: Sequence[dict[str, Any]], seed: int
) -> dict[str, Path]:
    files = write_architecture(out)
    plt = _plt()
    if plt is None:
        return files
    files.update(
        traces(
            plt,
            out,
            "sensor_swap_units",
            pick_patient(rows, "sensor_swap_units"),
            seed,
            "fig2_sensor_swap_traces",
        )
    )
    files.update(detection(plt, out, rows))
    files.update(harms(plt, out, rows))
    files.update(costs(plt, out, rows))
    return files


# ---- Fig 5: the change ledger --------------------------------------------------------------------


def ledger_figure(out: Path, rows: Sequence[dict[str, Any]]) -> dict[str, Path]:
    plt = _plt()
    if plt is None:
        return {}
    changes = list(dict.fromkeys(r["change"] for r in rows))
    fig, ax = plt.subplots(figsize=(3.6, 4.2))
    for i, ch in enumerate(changes):
        for j, arch in enumerate(L.ARCHS):
            r = next(x for x in rows if x["change"] == ch and x["architecture"] == arch)
            face = ARCH_COLORS[arch] if r["host_touched"] else "white"
            ax.add_patch(
                plt.Rectangle(
                    (j - 0.45, i - 0.4),
                    0.9,
                    0.8,
                    facecolor=face,
                    alpha=0.35,
                    edgecolor=ARCH_COLORS[arch],
                )
            )
            mark = (
                "" if r["inside_envelope"] is None else (" in" if r["inside_envelope"] else " OUT")
            )
            ax.text(
                j,
                i,
                f"{r['components_reverified']} / {r['organizations']}{mark}",
                ha="center",
                va="center",
                fontsize=6.5,
            )
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(len(changes) - 0.5, -0.5)
    ax.set_xticks(range(3), L.ARCHS)
    ax.set_yticks(range(len(changes)), [c.split("_", 1)[1].replace("_", " ") for c in changes])
    ax.set_xlabel("cell: components re-verified / organizations; shaded: controller host touched")
    ax.set_title(f"Change ledger, 10 declared changes ({LABEL})", fontsize=8)
    handles = [
        plt.Rectangle(
            (0, 0), 1, 1, facecolor=ARCH_COLORS[a], alpha=0.35, label=f"{a}, host touched"
        )
        for a in L.ARCHS
    ]
    ax.legend(
        handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=1, frameon=False
    )
    return _save(fig, out, "fig5_change_ledger")


# ---- Fig 7: the traceability matrix ----------------------------------------------------------------


def traceability_figure(out: Path) -> dict[str, Path]:
    from bridge_island.clinical import traceability as T

    plt = _plt()
    if plt is None:
        return {}
    rows = T.load()["rows"]
    fill = {"demonstrated": "#CDEBDD", "partial": "#FCE8C8"}
    cells = [
        [
            r["id"],
            f"{r['source']} {r['section']}",
            r["contract_field"],
            str(len(r["tests"])),
            r["status"],
        ]
        for r in rows
    ]
    fig, ax = plt.subplots(figsize=(3.6, 0.22 * len(rows) + 0.9))
    ax.axis("off")
    table = ax.table(
        cellText=cells,
        colLabels=["Id", "Source", "Contract field", "Tests", "Status"],
        cellColours=[["white"] * 4 + [fill.get(r["status"], "#EEEEEE")] for r in rows],
        loc="center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(5.5)
    table.auto_set_column_width(list(range(5)))
    table.scale(1, 1.15)
    ax.set_title(f"Requirements traceability ({LABEL}; not a compliance claim)", fontsize=7)
    return _save(fig, out, "fig7_traceability")
