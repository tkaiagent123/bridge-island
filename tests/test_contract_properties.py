"""Property tests: the contract's claims hold for random fault timings and patients.

Each example is a short bridge-contract run (synthetic, illustrative).
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bridge_island.clinical.loop import Fault, Loop, LoopScenario, build_loop, patients

HOURS = 5 * 3600.0
SETTINGS = settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


@st.composite
def data_faults(draw: st.DrawFn) -> tuple[Fault, ...]:
    """Glucose faults while the Bridge runs: dropouts, late bursts, bad values, throttling."""
    faults = []
    for _ in range(draw(st.integers(1, 3))):
        kind = draw(st.sampled_from(["sensor_dropout", "sensor_delay", "sensor_value"]))
        start = draw(st.integers(20, 160))  # every alarm resolves before the run ends
        minutes = draw(st.integers(5, 80))
        options: tuple[tuple[str, object], ...] = ()
        if kind == "sensor_delay":
            options = (("release_every_min", draw(st.integers(5, 40))),)
        elif kind == "sensor_value":
            options = (("readings", draw(st.integers(1, 3))), ("value", 600))
        faults.append(Fault(kind, clock(start), minutes, options))
    if draw(st.booleans()):
        faults.append(
            Fault("display_throttle", clock(draw(st.integers(20, 180))), 60, (("drop", 0.5),))
        )
    return tuple(faults)


def run(faults: tuple[Fault, ...], patient: int) -> Loop:
    loop = build_loop(
        "bridge-contract", patients(1)[patient], LoopScenario("p", duration_s=HOURS, faults=faults)
    )
    loop.run()
    return loop


@SETTINGS
@given(data_faults(), st.integers(0, 29))
def test_property_no_unflagged_stale_glucose(faults: tuple[Fault, ...], patient: int) -> None:
    """Claim C1: while the Bridge runs, the controller never acts on glucose older than the
    contract's maximum age without a fail-safe flag."""
    loop = run(faults, patient)
    limit = loop.bridge.contract.spec.max_age_s["GlucoseReading"]  # type: ignore[union-attr]
    old = [
        d
        for d in loop.controller.decisions
        if d.glucose is not None and (d.glucose_age_s or 0) > limit
    ]
    assert old == []


@SETTINGS
@given(
    st.integers(1, 11),
    st.integers(0, 29),
    st.booleans(),
    st.integers(30, 240),
)
def test_property_bolus_never_twice(delay: int, patient: int, silent: bool, silent_at: int) -> None:
    """Claim C2: no command id delivers its bolus twice, whether replayed or re-sent."""
    faults = [Fault("replay_command", "07:00", 0, (("delay_min", delay),))]
    if silent:
        faults.append(Fault("pump_silent", clock(420 + silent_at % 60), 60))
    loop = build_loop(
        "bridge-contract",
        patients(1)[patient],
        LoopScenario("p", duration_s=10 * 3600.0, faults=tuple(faults)),
    )
    loop.run()
    boluses: dict[object, int] = {}
    for _, cid, bolus, _ in loop.pump("PumpA").executed:
        if bolus > 0:
            boluses[cid] = boluses.get(cid, 0) + 1
    assert max(boluses.values(), default=0) <= 1  # a dead pump may deliver no bolus at all
    assert loop.replays  # the replay really happened


@SETTINGS
@given(data_faults(), st.integers(0, 29))
def test_property_failsafe_annunciated(faults: tuple[Fault, ...], patient: int) -> None:
    """Claim C3: every fail-safe entry has an annunciation that was acknowledged by the display,
    or an undelivered-alarm log entry with its age."""
    loop = run(faults, patient)
    contract = loop.bridge.contract  # type: ignore[union-attr]
    assert contract is not None
    entries = [e.time for e in loop.bridge.log.events if e.kind == "contract.failsafe"]  # type: ignore[union-attr]
    undelivered = {
        e.detail.split(" ")[0]
        for e in loop.bridge.log.events  # type: ignore[union-attr]
        if e.kind == "contract.alarm_undelivered"
    }
    for t in entries:
        alarm = next(
            a for a in contract.alarms.values() if a.kind == "failsafe" and a.raised_at == t
        )
        assert alarm.acked_at is not None or f"#{alarm.id}" in undelivered
