import pytest

from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message
from bridge_island.protocols.command_response import (
    BusController,
    BusError,
    CommandResponseBus,
    RemoteTerminal,
    Transfer,
)
from bridge_island.protocols.label_words import LabelBus, LabelBusError, Word
from bridge_island.protocols.pubsub import PubSubBus


def test_only_a_bus_controller_can_control_the_bus() -> None:
    bus = CommandResponseBus(Scheduler())
    with pytest.raises(BusError, match="remote terminal cannot"):
        bus.set_controller(RemoteTerminal("rt"))
    bus.set_controller(BusController("bc"))
    with pytest.raises(BusError, match="already"):
        bus.set_controller(BusController("bc2"))


def test_bus_cannot_start_without_controller() -> None:
    with pytest.raises(BusError):
        CommandResponseBus(Scheduler()).start()


def test_rates_must_divide_the_minor_frame() -> None:
    bus = CommandResponseBus(Scheduler(), minor_hz=100)
    assert bus.every_for(20) == 5
    with pytest.raises(BusError):
        bus.every_for(30)
    with pytest.raises(BusError):
        bus.every_for(200)


def test_transfers_both_directions_with_latency() -> None:
    s = Scheduler()
    bus = CommandResponseBus(s, latency_s=0.001)
    bc, rt = BusController("bc"), RemoteTerminal("rt")
    bus.set_controller(bc)
    addr = bus.attach(rt)
    got, replies = [], []
    rt.on_receive[1] = lambda m: got.append((s.now, m.spec_name))
    rt.on_transmit[2] = lambda: Message("reply", {}, 0.0)
    bc.transfers += [
        Transfer(addr, 1, "bc_to_rt", every=10, provide=lambda: Message("cmd", {}, s.now)),
        Transfer(addr, 2, "rt_to_bc", every=10, consume=replies.append),
    ]
    bus.start()
    s.run_until(0.25)
    assert [t for t, _ in got] == pytest.approx([0.001, 0.101, 0.201])
    assert len(replies) == 3 and bus.transfers_done == 6


def test_bus_reports_missing_terminals_and_subaddresses() -> None:
    s = Scheduler()
    bus = CommandResponseBus(s)
    bc, rt = BusController("bc"), RemoteTerminal("rt")
    bus.set_controller(bc)
    addr = bus.attach(rt)
    msg = Message("m", {}, 0.0)
    bc.transfers += [
        Transfer(9, 1, "bc_to_rt", provide=lambda: msg),
        Transfer(addr, 5, "bc_to_rt", provide=lambda: msg),
        Transfer(addr, 6, "rt_to_bc"),
        Transfer(addr, 7, "bc_to_rt", provide=lambda: None),
    ]
    bus.start()
    s.run_until(0.0)
    assert len(bus.errors) == 3


def test_a_bus_has_at_most_30_terminals() -> None:
    bus = CommandResponseBus(Scheduler())
    for i in range(30):
        bus.attach(RemoteTerminal(f"rt{i}"))
    with pytest.raises(BusError):
        bus.attach(RemoteTerminal("one too many"))


def test_label_bus_has_a_single_transmitter() -> None:
    s = Scheduler()
    bus = LabelBus(s, transmitter="tx", latency_s=0.01)
    got = []
    bus.subscribe(5, lambda w: got.append((s.now, w.value)))
    bus.transmit("tx", Word(5, 42, 0.0))
    bus.transmit("tx", Word(6, 1, 0.0))  # nobody listens
    with pytest.raises(LabelBusError):
        bus.transmit("rx", Word(5, 1, 0.0))
    s.run_until(1.0)
    assert got == [(0.01, 42)] and bus.words_sent == 2


def test_pubsub_delivers_to_topic_subscribers_only() -> None:
    s = Scheduler()
    bus = PubSubBus(s, latency_s=0.002)
    a, b = [], []
    bus.subscribe("x", a.append)
    bus.subscribe("y", b.append)
    bus.publish("x", 1)
    s.run_until(0.1)
    assert a == [1] and b == [] and bus.published == 1
