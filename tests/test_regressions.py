"""Bugs found by looking at the output, kept as tests."""

import world

from bridge_island.bridge.uid import UniqueInterfaceDistributor
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Representation
from bridge_island.core.events import EventLog
from bridge_island.unique.subsystem import Need, UniqueSubsystem


def test_a_slow_subsystem_recovers_after_a_stall_instead_of_going_stale() -> None:
    """The rate cap used to be measured in arrival time. The message released late after a stall
    used up the 1 Hz slot, the next fresh message was dropped as 'too soon', and StatusLogger
    ended the run with every input flagged stale although the Bridge had recovered."""
    system = world.build(1, seed=1)
    assert system.bridge is not None
    system.bridge.stall(2.2, 1.5)
    system.run(6.0)
    logger = system.subsystems["StatusLogger"]
    assert logger.check() == []
    source_times = [
        r.datum.source_time for r in logger.received if r.native_name == "bus_voltage_v"
    ]
    assert source_times == sorted(source_times) and max(source_times) >= 3.9  # fresh data after


def test_the_cap_is_measured_in_source_time_and_restarts_if_time_goes_backwards() -> None:
    sched = Scheduler()
    uid = UniqueInterfaceDistributor("uid", sched, EventLog(), lambda *_: None)
    rep = Representation("float64", "m", "f", "x")
    sub = UniqueSubsystem(sched, "S", "pubsub", (Need("v", rep, 1.0, 1.0, 5.0),))
    uid.connect(sub)
    # arrival times are bunched together, source times are 1 s apart: all are delivered
    for k, source in enumerate((0.0, 1.0, 2.0)):
        sched.at(0.1 * k, lambda s=source: uid.deliver("S", Datum("v", s, s)))
    # too close in source time: dropped; then source time restarts from zero: delivered
    sched.at(0.5, lambda: uid.deliver("S", Datum("v", 2.4, 2.4)))
    sched.at(0.6, lambda: uid.deliver("S", Datum("v", 0.0, 0.0)))
    sched.run_until(1.0)
    assert [r.datum.source_time for r in sub.received] == [0.0, 1.0, 2.0, 0.0]
    assert uid.decimated == 1
