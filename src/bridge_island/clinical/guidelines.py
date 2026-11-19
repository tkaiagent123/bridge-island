"""Guidelines G7 and G8, for the insulin loop only (``check-rules --example insulin-loop``).

* G7, clinical fail-safe first: every state in which the Bridge loses valid glucose data maps to
  the contract's fail-safe output (the controller gets a FAILSAFE-flagged reading, and every command
  carries the fail-safe basal until valid data returns).
* G8, degradation is visible: every fail-safe or alarm state produces an annunciation that reaches
  the CompanionDisplay or, if the display does not acknowledge it, is logged as undelivered with
  its age.

Each check runs short bridge-contract days for the first virtual patient, one per loss state.
Synthetic, illustrative.
"""

from __future__ import annotations

from types import ModuleType

from bridge_island.clinical.loop import Fault, Loop, LoopScenario, build_loop, patients

HOURS = 4 * 3600.0
LOSS_STATES: dict[str, tuple[Fault, ...]] = {
    "readings stop": (Fault("sensor_dropout", "01:00", 45),),
    "readings arrive late": (Fault("sensor_delay", "01:00", 60, (("release_every_min", 30),)),),
    "value out of range": (Fault("sensor_value", "01:00", 0, (("readings", 2), ("value", 600))),),
    "Bridge stalls": (Fault("bridge_stall", "01:00", 20),),
}
VISIBILITY = {
    **LOSS_STATES,
    "pump stops confirming": (Fault("pump_silent", "01:00", 60),),
    "display throttled": (
        Fault("sensor_dropout", "01:00", 45),
        Fault("display_throttle", "00:55", 90, (("drop", 0.5),)),
    ),
}


def _run(name: str, faults: tuple[Fault, ...]) -> Loop:
    loop = build_loop(
        "bridge-contract", patients(1)[0], LoopScenario(name, duration_s=HOURS, faults=faults)
    )
    loop.run()
    return loop


def failsafe_problems(loop: Loop) -> list[str]:
    """Why this run breaks G7, if it does."""
    bridge = loop.bridge
    assert bridge is not None and bridge.contract is not None
    spec = bridge.contract.spec
    events = bridge.log.events
    entered = [e.time for e in events if e.kind == "contract.failsafe"]
    if not entered:
        return ["the contract never entered fail-safe"]
    problems = []
    flagged = [
        t
        for t, m in loop.platform.accepted
        if m.spec_name == "GlucoseReading" and m.values["sensor_status"] == spec.failsafe_status
    ]
    if not flagged:
        problems.append("the controller never received a FAILSAFE-flagged reading")
    marks = sorted(
        (e.time, e.kind) for e in events if e.kind in ("contract.failsafe", "contract.recovered")
    )
    spans, since = [], None
    for t, kind in marks:
        if kind == "contract.failsafe" and since is None:
            since = t
        elif kind == "contract.recovered" and since is not None:
            spans.append((since, t))
            since = None
    if since is not None:
        spans.append((since, loop.scenario.duration_s))
    for name in (n for n in loop.devices if n.startswith("Pump")):
        for t, cid, _, basal in loop.pump(name).executed:
            issued = loop.controller.issued.get(cid)
            inside = any(a < issued[0] < b for a, b in spans) if issued else False
            if (
                inside
                and basal is not None
                and abs(basal - (spec.failsafe_basal_u_h or 0.0)) > 1e-9
            ):
                problems.append(f"command {cid} at {t:.0f} s ran basal {basal} during fail-safe")
    return problems


def visibility_problems(loop: Loop) -> list[str]:
    """Why this run breaks G8, if it does."""
    bridge = loop.bridge
    assert bridge is not None and bridge.contract is not None
    problems = []
    undelivered = {
        e.detail.split(" ")[0] for e in bridge.log.events if e.kind == "contract.alarm_undelivered"
    }
    for alarm in bridge.contract.alarms.values():
        if alarm.acked_at is None and f"#{alarm.id}" not in undelivered:
            problems.append(
                f"alarm #{alarm.id} ({alarm.kind}) neither acknowledged nor logged undelivered"
            )
    if any(e.kind == "contract.failsafe" for e in bridge.log.events) and not bridge.contract.alarms:
        problems.append("fail-safe was entered without an alarm")
    return problems


def g7(world: ModuleType, version: int) -> tuple[bool, str]:
    if version != 1:
        return (
            True,
            "the contract is written for baseline v1; v2 is outside its envelope (not checked)",
        )
    found = {name: failsafe_problems(_run(name, faults)) for name, faults in LOSS_STATES.items()}
    bad = {k: v for k, v in found.items() if v}
    ok = len(found) - len(bad)
    detail = f"{ok} of {len(found)} loss states end in the contract's fail-safe output"
    return not bad, detail + ("" if not bad else f"; {bad}")


def g8(world: ModuleType, version: int) -> tuple[bool, str]:
    if version != 1:
        return (
            True,
            "the contract is written for baseline v1; v2 is outside its envelope (not checked)",
        )
    loops = {name: _run(name, faults) for name, faults in VISIBILITY.items()}
    found = {name: visibility_problems(loop) for name, loop in loops.items()}
    alarms = sum(
        len(lp.bridge.contract.alarms) for lp in loops.values() if lp.bridge and lp.bridge.contract
    )
    bad = {k: v for k, v in found.items() if v}
    detail = (
        f"{alarms} alarms in {len(found)} degraded states: each acknowledged by the display "
        "or logged undelivered with its age"
    )
    return not bad, detail + ("" if not bad else f"; {bad}")


RULES = [("G7", "Clinical fail-safe first", g7), ("G8", "Degradation is visible", g8)]
