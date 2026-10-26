"""Unique Interface Distributor (UID): one separate connection per unique system.

Each connection uses that system's own native protocol. Toward the unique side the UID
plays the controlling role: bus controller on a command/response bus, sole transmitter on a
label-word bus, publisher on a pub/sub bus.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Message
from bridge_island.core.events import EventLog
from bridge_island.protocols.command_response import (
    BusController,
    CommandResponseBus,
    RemoteTerminal,
    Transfer,
)
from bridge_island.protocols.label_words import LabelBus, Word
from bridge_island.protocols.pubsub import PubSubBus
from bridge_island.unique.subsystem import UniqueSubsystem

Status = tuple[str, str, str]  # ("unavailable", native, reason) | ("annunciation", kind, detail)
Uplink = Callable[[str, Message], None]

_STATUS_SA, _UPLINK_SA, _STATUS_LABEL, _UPLINK_LABEL = 30, 29, 0o377, 0o200


class Port(Protocol):
    def send(self, native: str, datum: Datum) -> None: ...
    def send_status(self, status: Status) -> None: ...


def _apply_status(sub: UniqueSubsystem, status: Status) -> None:
    kind, a, b = status
    if kind == "unavailable":
        sub.on_unavailable(a, b)
    else:
        sub.on_annunciation(a, b)


class PubSubPort:
    def __init__(self, sched: Scheduler, sub: UniqueSubsystem, on_uplink: Uplink) -> None:
        self.bus = PubSubBus(sched, sub.bus_latency_s or 0.002)
        for need in sub.needs:
            self.bus.subscribe(
                f"data/{need.native_name}",
                lambda d, n=need.native_name: sub.on_data(n, d),  # type: ignore[misc]
            )
        self.bus.subscribe("status", lambda s: _apply_status(sub, s))
        self.bus.subscribe("up", lambda m: on_uplink(sub.name, m))
        sub._uplink = lambda m: self.bus.publish("up", m)

    def send(self, native: str, datum: Datum) -> None:
        self.bus.publish(f"data/{native}", datum)

    def send_status(self, status: Status) -> None:
        self.bus.publish("status", status)


class LabelWordsPort:
    def __init__(self, sched: Scheduler, sub: UniqueSubsystem, on_uplink: Uplink) -> None:
        latency = sub.bus_latency_s or 0.0002
        self.down = LabelBus(sched, "uid", latency)
        self.up = LabelBus(sched, sub.name, latency)
        self.labels = {n.native_name: 0o100 + i for i, n in enumerate(sub.needs)}
        for native, label in self.labels.items():
            self.down.subscribe(
                label,
                lambda w, n=native: sub.on_data(n, Datum(n, w.value, w.source_time)),  # type: ignore[misc]
            )
        self.down.subscribe(_STATUS_LABEL, lambda w: _apply_status(sub, w.value))
        self.up.subscribe(_UPLINK_LABEL, lambda w: on_uplink(sub.name, w.value))
        sub._uplink = lambda m: self.up.transmit(sub.name, Word(_UPLINK_LABEL, m, m.source_time))

    def send(self, native: str, datum: Datum) -> None:
        self.down.transmit("uid", Word(self.labels[native], datum.value, datum.source_time))

    def send_status(self, status: Status) -> None:
        self.down.transmit("uid", Word(_STATUS_LABEL, status, 0.0))


class CommandResponsePort:
    """The UID is the bus controller; the subsystem is a remote terminal."""

    def __init__(self, sched: Scheduler, sub: UniqueSubsystem, on_uplink: Uplink) -> None:
        self.bus = CommandResponseBus(sched, latency_s=sub.bus_latency_s or 0.0005)
        bc = BusController("uid")
        rt = RemoteTerminal(sub.name)
        self.bus.set_controller(bc)
        addr = self.bus.attach(rt)
        self.pending: dict[str, Message] = {}
        self.status_queue: deque[Message] = deque()
        self.up_queue: deque[Message] = deque()
        for i, need in enumerate(sub.needs):
            sa, native = i + 1, need.native_name
            bc.transfers.append(
                Transfer(
                    addr,
                    sa,
                    "bc_to_rt",
                    every=self.bus.every_for(min(need.rate_hz, self.bus.minor_hz)),
                    provide=lambda n=native: self.pending.pop(n, None),  # type: ignore[misc]
                )
            )
            rt.on_receive[sa] = lambda m, n=native: sub.on_data(  # type: ignore[misc]
                n, Datum(n, m.values["value"], m.source_time)
            )
        bc.transfers.append(
            Transfer(
                addr,
                _STATUS_SA,
                "bc_to_rt",
                provide=lambda: self.status_queue.popleft() if self.status_queue else None,
            )
        )
        rt.on_receive[_STATUS_SA] = lambda m: _apply_status(sub, m.values["status"])
        rt.on_transmit[_UPLINK_SA] = lambda: self.up_queue.popleft() if self.up_queue else None
        bc.transfers.append(
            Transfer(
                addr,
                _UPLINK_SA,
                "rt_to_bc",
                every=self.bus.every_for(10),
                consume=lambda m: on_uplink(sub.name, m) if m else None,
            )
        )
        sub._uplink = self.up_queue.append
        self.bus.start()

    def send(self, native: str, datum: Datum) -> None:
        self.pending[native] = Message(native, {"value": datum.value}, datum.source_time)

    def send_status(self, status: Status) -> None:
        self.status_queue.append(Message("status", {"status": status}, 0.0))


_PORTS: dict[str, Any] = {
    "pubsub": PubSubPort,
    "label_words": LabelWordsPort,
    "command_response": CommandResponsePort,
}


class UniqueInterfaceDistributor:
    island = "bridge"

    def __init__(self, name: str, sched: Scheduler, log: EventLog, on_uplink: Uplink) -> None:
        self.name = name
        self.sched = sched
        self.log = log
        self.on_uplink = on_uplink
        self.subsystems: dict[str, UniqueSubsystem] = {}
        self._ports: dict[str, Port] = {}
        self._last_sent: dict[tuple[str, str], float] = {}
        self.sent = 0
        self.decimated = 0

    def connect(self, sub: UniqueSubsystem) -> None:
        if sub.protocol not in _PORTS:
            raise ValueError(f"{sub.name}: unknown native protocol {sub.protocol!r}")
        self.subsystems[sub.name] = sub
        self._ports[sub.name] = _PORTS[sub.protocol](self.sched, sub, self.on_uplink)

    def deliver(self, subsystem: str, datum: Datum) -> bool:
        """Send a datum on the subsystem's native connection, capped at its useful rate."""
        need = self.subsystems[subsystem].need(datum.element)
        if need is None:
            raise KeyError(f"{subsystem} has no input {datum.element!r}")
        key = (subsystem, datum.element)
        last = self._last_sent.get(key)
        if last is not None and self.sched.now - last < 1.0 / need.rate_hz - 1e-9:
            self.decimated += 1
            return False
        self._last_sent[key] = self.sched.now
        self._ports[subsystem].send(datum.element, datum)
        self.sent += 1
        return True

    def deliver_unavailable(self, subsystem: str, native: str, reason: str) -> None:
        self._ports[subsystem].send_status(("unavailable", native, reason))

    def announce(self, subsystems: list[str], kind: str, detail: str) -> None:
        for name in subsystems:
            self._ports[name].send_status(("annunciation", kind, detail))
