"""What one insulin-loop run shows: glucose outcomes, interface outcomes and the Bridge's costs.

Glucose thresholds follow the international consensus on time in range (Battelino et al.,
Diabetes Care 2019, PMID 31177185): 70-180 mg/dL, below 70, below 54, above 180, above 250. They are
computed on the model's plasma glucose, once a minute. Synthetic, illustrative: these are counts on
an invented model, not clinical results.
"""

from __future__ import annotations

import statistics
from typing import Any

from bridge_island.clinical.controller import ALARMS
from bridge_island.clinical.loop import Loop, fault_windows

DETECTING = (
    "tm.reject",
    "tm.reject_up",
    "cyber.block",
    "oiu.reject_in",
    "oiu.reject_out",
    "health.stale",
    "health.discontinuity",
    "health.out_of_order",
    "health.bridge_stall",
    "contract.failsafe",
    "contract.violation",
    "contract.block",
    "contract.replay_blocked",
    "contract.resend",
    "contract.unconfirmed",
    "contract.alarm_undelivered",
)
HOST_FAULT_ALARMS = ("NO_GLUCOSE", "PUMP_COMM")
TOLD = ("contract.failsafe", "contract.unconfirmed", "contract.replay_blocked")
ALARM_ON_TIME_S = 300.0


def glucose_outcomes(trace: list[float], minutes: int) -> dict[str, Any]:
    g = trace[:minutes]
    n = len(g)
    counts = {
        "tir_70_180_min": sum(1 for v in g if 70 <= v <= 180),
        "below_70_min": sum(1 for v in g if v < 70),
        "below_54_min": sum(1 for v in g if v < 54),
        "above_180_min": sum(1 for v in g if v > 180),
        "above_250_min": sum(1 for v in g if v > 250),
    }
    out: dict[str, Any] = dict(counts)
    for key, value in counts.items():
        out[key.replace("_min", "_share")] = round(value / n, 4) if n else 0.0
    out["glucose_min"] = round(min(g), 1)
    out["glucose_max"] = round(max(g), 1)
    return out


def detections(loop: Loop) -> list[tuple[float, str, str]]:
    """Every report that something at the boundary is wrong: (time, by whom, what)."""
    found: list[tuple[float, str, str]] = []
    for name, adapter in loop.adapters.items():
        found += [(t, f"{name} adapter", f"{ref}: {why}") for t, ref, why in adapter.errors]
    found += loop.adapter_errors
    found += [(t, "controller host", why) for t, why in loop.platform.rejected]
    found += [(t, "controller", a) for t, a in loop.controller.alarm_log if a in HOST_FAULT_ALARMS]
    if loop.bridge is not None:
        found += [
            (e.time, e.source, f"{e.kind}: {e.detail}")
            for e in loop.bridge.log.events
            if e.kind in DETECTING
        ]
    return sorted(found, key=lambda d: d[0])


def told_events(loop: Loop) -> list[tuple[float, str, str]]:
    """Reports that reached the controller (a fault alarm of its own, a fail-safe flag or an
    unconfirmed report from the Bridge) or the display (any annunciation it showed)."""
    found = [(t, "controller", a) for t, a in loop.controller.alarm_log if a in HOST_FAULT_ALARMS]
    if loop.bridge is not None:
        found += [
            (e.time, e.source, f"{e.kind}: {e.detail}")
            for e in loop.bridge.log.events
            if e.kind in TOLD
        ]
    found += [(t, "display", f"{kind}: {detail}") for t, kind, detail in loop.display.annunciations]
    return sorted(found, key=lambda d: d[0])


def _intervals(
    events: list[tuple[float, str]], start_kind: str, end_kind: str, end: float
) -> list[tuple[float, float]]:
    out, since = [], None
    for t, kind in events:
        if kind == start_kind and since is None:
            since = t
        elif kind == end_kind and since is not None:
            out.append((since, t))
            since = None
    if since is not None:
        out.append((since, end))
    return out


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _detection(loop: Loop, row: dict[str, Any], windows: list[tuple[str, float, float]]) -> None:
    """Detection: "told" (the controller or display was told) and "logged" (written somewhere)."""
    onset = min((w[1] for w in windows), default=None)
    if loop.replays:  # a replay waits for its command: the fault starts when it is injected
        onset = loop.replays[0][0]
    told = told_events(loop)
    first = next((d for d in told if onset is not None and d[0] >= onset), None)
    logged = next((d for d in detections(loop) if onset is not None and d[0] >= onset), None)
    row["fault_onset_s"] = onset
    row["detected"] = None if onset is None else first is not None
    row["time_to_detect_s"] = None if first is None or onset is None else round(first[0] - onset, 1)
    row["detected_by"] = None if first is None else first[1]
    row["time_to_first_log_s"] = (
        None if logged is None or onset is None else round(logged[0] - onset, 1)
    )


def _dosing(loop: Loop, row: dict[str, Any], windows: list[tuple[str, float, float]]) -> None:
    """The controller acting on old data, and commands without confirmation."""
    end = loop.scenario.duration_s
    c = loop.controller
    max_age = 660.0
    row["unsafe_age_min"] = round(
        sum(
            c.settings.cycle_s
            for d in c.decisions
            if d.glucose is not None and (d.glucose_age_s or 0) > max_age
        )
        / 60.0,
        1,
    )
    row["commands_issued"] = len(c.issued)
    row["commands_unconfirmed"] = (
        sum(  # the last few minutes' commands have no time to be confirmed
            1 for cid, (t, _, _) in c.issued.items() if cid not in c.confirmed and t < end - 600.0
        )
    )
    commanded = sum(d.bolus_u + d.basal_u_h * c.settings.cycle_s / 3600.0 for d in c.decisions)
    row["insulin_delivered_u"] = round(loop.patient.delivered_u, 2)
    row["insulin_commanded_u"] = round(commanded, 2)
    row["delivered_minus_commanded_u"] = round(loop.patient.delivered_u - commanded, 2)


def _unit_errors(loop: Loop, row: dict[str, Any], windows: list[tuple[str, float, float]]) -> None:
    """Unit or encoding errors that reached the controller or a pump."""
    c = loop.controller
    meant = {}
    for name in (n for n in loop.devices if n.startswith("Sensor")):
        meant.update({t: (v, s) for t, v, s in loop.sensor(name).sent})
    to_controller = 0
    for _, msg in loop.platform.accepted:
        if msg.spec_name != "GlucoseReading" or msg.values["sensor_status"] not in (0, 1, 2):
            continue
        truth = meant.get(msg.values["source_time"])
        if truth is not None and abs(msg.values["glucose"] - truth[0]) > max(5.0, 0.05 * truth[0]):
            to_controller += 1
    to_pump = 0
    for name in (n for n in loop.devices if n.startswith("Pump")):
        seen: set[Any] = set()
        for _, cid, bolus, basal in loop.pump(name).executed:
            issued = c.issued.get(cid)
            if issued is None or cid in seen:
                continue
            seen.add(cid)
            if abs(bolus - issued[2]) > 0.06 or basal is None:
                to_pump += 1
    row["unit_errors_to_controller"] = to_controller
    row["unit_errors_to_pump"] = to_pump


def _alarms(loop: Loop, row: dict[str, Any], windows: list[tuple[str, float, float]]) -> None:
    """Alarms raised, and whether they reached the display."""
    end = loop.scenario.duration_s
    c = loop.controller
    shown = loop.display.alarms_shown
    raised: list[tuple[float, str]] = [(t, f"host:{a}") for t, a in c.alarm_log]
    contract = loop.bridge.contract if loop.bridge is not None else None
    if contract is not None:
        raised += [(a.raised_at, f"#{a.id} ") for a in contract.alarms.values()]
    on_time, late, lost = 0, [], []
    for t, key in raised:
        hit = next((s for s, what in shown if s >= t and key in what), None)
        if hit is None:
            lost.append(end - t)
        elif hit - t <= ALARM_ON_TIME_S:
            on_time += 1
        else:
            late.append(hit - t)
    row["alarms_raised"] = len(raised)
    row["alarms_on_time"] = on_time
    row["alarms_late"] = len(late)
    row["alarms_lost"] = len(lost)
    row["alarm_max_delay_s"] = round(max(late + lost), 0) if late or lost else 0.0
    row["undelivered_logged"] = (
        sum(1 for e in loop.bridge.log.events if e.kind == "contract.alarm_undelivered")
        if loop.bridge is not None
        else 0
    )


def _costs(loop: Loop, row: dict[str, Any], windows: list[tuple[str, float, float]]) -> None:
    """What the bridge costs: latency, unneeded fail-safe time, what fails with it."""
    end = loop.scenario.duration_s
    c = loop.controller
    glucose_lat = [
        t - m.values["source_time"]
        for t, m in loop.platform.accepted
        if m.spec_name == "GlucoseReading" and m.values["sensor_status"] != 4
    ]
    first_exec: dict[Any, float] = {}
    for name in (n for n in loop.devices if n.startswith("Pump")):
        for t, cid, _, _ in loop.pump(name).executed:
            first_exec.setdefault(cid, t)
    pump_lat = [first_exec[cid] - c.issued[cid][0] for cid in first_exec if cid in c.issued]
    row["sensor_to_host_median_s"] = (
        round(statistics.median(glucose_lat), 1) if glucose_lat else None
    )
    row["sensor_to_host_peak_s"] = round(max(glucose_lat), 1) if glucose_lat else None
    row["host_to_pump_median_s"] = round(statistics.median(pump_lat), 1) if pump_lat else None
    row["host_to_pump_peak_s"] = round(max(pump_lat), 1) if pump_lat else None
    failsafe = []
    if loop.bridge is not None:
        marks = [
            (e.time, e.kind)
            for e in loop.bridge.log.events
            if e.kind in ("contract.failsafe", "contract.recovered")
        ]
        failsafe = _intervals(marks, "contract.failsafe", "contract.recovered", end)
    needed = [(s, e + 900.0) for _, s, e in windows]
    total = sum(b - a for a, b in failsafe)
    unneeded = total - sum(_overlap(f, w) for f in failsafe for w in needed)
    row["failsafe_min"] = round(total / 60.0, 1)
    row["failsafe_unneeded_min"] = round(max(0.0, unneeded) / 60.0, 1)
    row["paths_failed_in_stall"] = _paths_failed(loop, windows)


def measure(loop: Loop) -> dict[str, Any]:
    """One row of results for one run."""
    windows = fault_windows(loop.scenario, loop.meals)
    row: dict[str, Any] = {
        "scenario": loop.scenario.name,
        "patient": loop.params.id,
        "architecture": loop.arch,
    }
    row.update(glucose_outcomes(loop.patient.trace, int(loop.scenario.duration_s // 60)))
    for part in (_detection, _dosing, _unit_errors, _alarms, _costs):
        part(loop, row, windows)
    return row


def _paths_failed(loop: Loop, windows: list[tuple[str, float, float]]) -> int | None:
    """During a Bridge stall: how many device paths carried nothing (sensor, pump, display)."""
    stall = next(((s, e) for k, s, e in windows if k == "bridge_stall"), None)
    if stall is None:
        return None
    s, e = stall
    lo, hi = s + 360.0, e  # allow the last in-flight item; a path that is alive moves within 6 min
    glucose = [
        t for t, m in loop.platform.accepted if m.spec_name == "GlucoseReading" and lo <= t <= hi
    ]
    pump = [
        t
        for n in loop.devices
        if n.startswith("Pump")
        for t, *_ in loop.pump(n).executed
        if lo <= t <= hi
    ]
    display = [r.delivered_at for r in loop.display.received if lo <= r.delivered_at <= hi]
    return sum(1 for path in (glucose, pump, display) if not path)


def critical_events(loop: Loop) -> list[dict[str, Any]]:
    """The critical event record: commands and confirmations, malfunctions, alarms and
    acknowledgments, connectivity lost and restored. Bridge-contract runs carry the Bridge's log."""
    rows: list[dict[str, Any]] = []

    def add(t: float, category: str, source: str, detail: str) -> None:
        rows.append(
            {
                "time_s": round(t, 1),
                "clock": _clock(t),
                "category": category,
                "source": source,
                "detail": detail,
            }
        )

    for d in loop.controller.decisions:
        add(
            d.time,
            "command",
            "controller",
            f"command {d.command_id}: basal {d.basal_u_h:.2f} U/h, bolus {d.bolus_u:g} U, "
            f"mode {d.mode}",
        )
    for t, m in loop.platform.accepted:
        if m.spec_name == "DeliveryConfirmation":
            v = m.values
            status = "UNCONFIRMED (told by the Bridge)" if v["pump_status"] == 3 else "confirmed"
            add(
                t,
                "confirmation",
                "controller host",
                f"command {v['command_id']} {status}, {v['delivered']:g} U",
            )
        elif m.spec_name == "AlarmAck":
            add(
                t,
                "acknowledgment",
                "controller host",
                f"alarm #{m.values['alarm_id']} acknowledged by the display",
            )
    for t, a in loop.controller.alarm_log:
        add(t, "alarm", "controller", f"{a} (code {ALARMS.index(a)})")
    if loop.bridge is not None:
        kinds = {
            "contract.failsafe": "malfunction",
            "contract.violation": "malfunction",
            "contract.recovered": "connectivity",
            "contract.block": "malfunction",
            "contract.replay_blocked": "malfunction",
            "contract.resend": "command",
            "contract.unconfirmed": "malfunction",
            "contract.alarm": "alarm",
            "contract.alarm_undelivered": "alarm",
            "contract.ack": "acknowledgment",
            "contract.failsafe_basal": "command",
            "health.stale": "connectivity",
            "health.recovered": "connectivity",
            "health.bridge_stall": "connectivity",
            "bridge.stall": "connectivity",
            "bridge.resume": "connectivity",
            "tm.reject_up": "malfunction",
            "tm.reject": "malfunction",
            "cyber.block": "malfunction",
        }
        for e in loop.bridge.log.events:
            if e.kind in kinds:
                add(e.time, kinds[e.kind], e.source, f"{e.kind}: {e.detail}")
    order = {
        "connectivity": 0,
        "malfunction": 1,
        "alarm": 2,
        "command": 3,
        "confirmation": 4,
        "acknowledgment": 5,
    }
    return sorted(rows, key=lambda r: (r["time_s"], order[r["category"]], r["detail"]))


def _clock(t: float) -> str:
    m = int(t // 60)
    return f"{m // 60:02d}:{m % 60:02d}"
