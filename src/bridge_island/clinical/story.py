"""``bridge-island demo insulin-loop --story``: a two-minute narrative, with the counts at every step.

One virtual patient, five beats: the nominal day, the sensor swap, the pump firmware change (where a
stale direct adapter mis-doses without a word), the pump going silent, and the Bridge itself
stalling. Synthetic, illustrative; not medical software.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bridge_island.clinical import loop as L
from bridge_island.clinical import metrics as M
from bridge_island.clinical import scenarios as S

NOTE = "Synthetic, illustrative. Not medical software; not a validated model or algorithm."
BEATS = (
    ("nominal", "A nominal day"),
    ("sensor_swap_units", "08:00: SensorA is replaced by SensorB (mmol/L, every minute)"),
    (
        "pump_fw_v2",
        "08:00: the pump moves to firmware v2 (bolus in 0.05 U pulses, a renamed field)",
    ),
    ("pump_silent", "After lunch: the pump stops confirming, then stops delivering"),
    ("bridge_stall", "After lunch: the Bridge itself stalls for 20 minutes"),
)


def _clock(seconds: float | None) -> str:
    return "never" if seconds is None else f"{seconds / 60:.0f} min"


def story(patient: str = "VP01", seed: int = 1) -> dict[str, Any]:
    index = int(patient[2:]) - 1
    out: dict[str, Any] = {"patient": patient, "seed": seed, "note": NOTE, "beats": []}
    for name, title in BEATS:
        rows = {}
        for arch in L.ARCHS:
            loop = L.build_loop(arch, L.patients(seed)[index], S.by_name(name))
            loop.run()
            rows[arch] = M.measure(loop)
        out["beats"].append(
            {"scenario": name, "title": title, "results": rows, "says": _says(name, rows)}
        )
    return out


def _says(name: str, r: dict[str, dict[str, Any]]) -> list[str]:
    d, b, c = r["direct"], r["bridge-basic"], r["bridge-contract"]
    if name == "nominal":
        return [
            f"All three keep glucose in 70-180 mg/dL for {d['tir_70_180_min']}, {b['tir_70_180_min']} and "
            f"{c['tir_70_180_min']} minutes of 1440: when nothing goes wrong they perform alike.",
            f"The cost shows already: a command reaches the pump in {d['host_to_pump_median_s']:g} s direct, "
            f"{c['host_to_pump_median_s']:g} s through the Bridge (median).",
        ]
    if name == "sensor_swap_units":
        return [
            f"Direct: the old adapter rejects every new reading ({d['time_to_first_log_s'] or 0:.0f} s after the swap, "
            f"in a log nobody reads); the controller acts on stale glucose for {d['unsafe_age_min']:g} min and "
            f"is told only after {_clock(d['time_to_detect_s'])}, by its own watchdog.",
            f"Bridge: the mapping converts mmol/L to mg/dL, inside the change envelope; "
            f"{c['unit_errors_to_controller']} unit errors reach the controller and the contract raises nothing.",
        ]
    if name == "pump_fw_v2":
        return [
            f"Direct: the old adapter's 'units' arrive as pulses: {d['unit_errors_to_pump']} commands mis-dosed, "
            f"{-d['delivered_minus_commanded_u']:.1f} U less insulin than commanded, "
            f"{d['above_250_min']} min above 250 mg/dL. The controller is told after {_clock(d['time_to_detect_s'])}, "
            "and only that the pump is not confirming.",
            f"Bridge: one mapping entry quantizes units to pulses; {c['unit_errors_to_pump']} errors reach the pump.",
        ]
    if name == "pump_silent":
        return [
            f"Direct and bridge-basic: the host's 30-minute watchdog tells the controller after "
            f"{_clock(d['time_to_detect_s'])}.",
            f"Bridge with the contract: re-sent twice (never the bolus), then alarmed and the controller told "
            f"after {_clock(c['time_to_detect_s'])}; {c['alarms_on_time']} of {c['alarms_raised']} alarms reached the "
            "display on time.",
        ]
    return [
        f"Direct has no Bridge to stall: {d['paths_failed_in_stall']} device paths fail.",
        f"With the Bridge, {c['paths_failed_in_stall']} of 3 paths carry nothing for the whole stall, and the "
        f"controller uses unflagged stale glucose for {c['unsafe_age_min']:g} min (the contract stalls too). "
        "The pump falls back to its programmed basal when its last command expires.",
        f"The health monitor notices only on resume, {_clock(c['time_to_detect_s'])} after the stall began. "
        "Detection is not tolerance: the Bridge needs its own redundancy and safety case.",
    ]


def render(data: dict[str, Any], out: Callable[[str], None] = print) -> None:
    out(
        f"Generic Glycemic Loop, virtual patient {data['patient']}, seed {data['seed']}. {data['note']}"
    )
    for i, beat in enumerate(data["beats"], 1):
        out(f"\n{i}. {beat['title']}")
        for line in beat["says"]:
            out(f"   - {line}")
    out(
        "\nThe Bridge moves interface knowledge into one place and checks it there; it does not make it disappear,"
    )
    out(
        "and it is one more thing that can fail. All counts are from this run: synthetic, illustrative."
    )
