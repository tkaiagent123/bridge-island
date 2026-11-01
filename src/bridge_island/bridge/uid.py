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
                lambda w, n=native: sub.on_data(  # type: ignore[misc]
                    n, Datum(n, w.value, w.source_time, w.estimated, w.basis_time)
                ),
            )
        self.down.subscribe(_STATUS_LABEL, lambda w: _apply_status(sub, w.value))
        self.up.subscribe(_UPLINK_LABEL, lambda w: on_uplink(sub.name, w.value))
        sub._uplink = lambda m: self.up.transmit(sub.name, Word(_UPLINK_LABEL, m, m.source_time))

    def send(self, native: str, datum: Datum) -> None:
        self.down.transmit(
            "uid",
            Word(
                self.labels[native],
                datum.value,
                datum.source_time,
                datum.estimated,
                datum.basis_time,
            ),
        )

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
                n,
                Datum(
                    n,
                    m.values["value"],
                    m.source_time,
                    m.values.get("estimated", False),
                    m.values.get("basis_time"),
                ),
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
        values = {
            "value": datum.value,
            "estimated": datum.estimated,
            "basis_time": datum.basis_time,
        }
        self.pending[native] = Message(native, values, datum.source_time)

    def send_status(self, status: Status) -> None:
        self.status_queue.append(Message("status", {"status": status}, 0.0))


_PORTS: dict[str, Any] = {
    "pubsub": PubSubPort,
    "label_words": LabelWordsPort,
    "command_response": CommandResponsePort,
}


class UniqueInterfaceDistributor:
    island = "bridge"

    def __init__(
        self,
        name: str,
        sched: Scheduler,
        log: EventLog,
        on_uplink: Uplink,
        rate_policy: str = "cap",
        max_extrapolation_s: float | None = None,
        is_running: Callable[[], bool] = lambda: True,
    ) -> None:
        """``rate_policy`` says what to do when a subsystem can use a higher rate than the
        baseline supplies: ``"cap"`` delivers only real samples, at most at the subsystem's rate;
        ``"extrapolate"`` delivers one item per period of the subsystem's rate: the freshest real
        sample if one arrived, otherwise an *estimate* (linear for floats, hold for other
        encodings), for at most ``max_extrapolation_s`` after the last real sample (default: 1.5
        periods of that input's source, so a dead source is not masked). A source that
        is already as fast as the subsystem needs is handled exactly as with ``"cap"``. Neither
        policy creates information the baseline did not send: both keep the interface working."""
        if rate_policy not in ("cap", "extrapolate"):
            raise ValueError(f"unknown rate policy {rate_policy!r}; use 'cap' or 'extrapolate'")
        self.name = name
        self.sched = sched
        self.log = log
        self.on_uplink = on_uplink
        self.rate_policy = rate_policy
        self.max_extrapolation_s = max_extrapolation_s
        self._is_running = is_running
        self.subsystems: dict[str, UniqueSubsystem] = {}
        self._ports: dict[str, Port] = {}
        self._last_sent: dict[tuple[str, str], float] = {}
        self._history: dict[tuple[str, str], deque[tuple[float, Any]]] = {}
        self._pending: dict[tuple[str, str], Datum] = {}
        self._gridded: set[tuple[str, str]] = set()
        self._ticking: set[tuple[str, str]] = set()
        self.sent = 0
        self.decimated = 0
        self.estimates = 0

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
        history = self._history.setdefault(key, deque(maxlen=2))
        history.append((datum.source_time, datum.value))
        period = 1.0 / need.rate_hz
        slow = len(history) == 2 and history[1][0] - history[0][0] > period * 1.01
        if self.rate_policy == "extrapolate" and slow:
            # The source is slower than the subsystem can use: deliver on the subsystem's own
            # rate grid, one slot per period, carrying the freshest real sample if one has
            # arrived and an estimate otherwise. Real samples wait for their slot.
            self._gridded.add(key)
            self._pending[key] = datum
            if key not in self._ticking:
                self._ticking.add(key)
                self.sched.every(period, lambda: self._tick(key), start=self.sched.now + period)
            return True
        self._gridded.discard(key)
        self._pending.pop(key, None)
        # The cap is measured in *source* time: it limits the rate of the data, not of its
        # arrival. (Measured in arrival time, a message released late after a stall would use up
        # the slot and a fresh one 0.3 s later would be dropped.) If source time goes backwards,
        # for example after a source restart, accept the datum and start counting again.
        last = self._last_sent.get(key)
        if last is not None and 0 <= datum.source_time - last < period - 1e-6:
            self.decimated += 1
            return False
        self._last_sent[key] = datum.source_time
        self._ports[subsystem].send(datum.element, datum)
        self.sent += 1
        return True

    def _tick(self, key: tuple[str, str]) -> None:
        """One slot of the subsystem's rate grid: a real sample if pending, else an estimate."""
        if key not in self._gridded or not self._is_running():
            return
        subsystem, native = key
        pending = self._pending.pop(key, None)
        if pending is not None:
            self._ports[subsystem].send(native, pending)
            self.sent += 1
            return
        need = self.subsystems[subsystem].need(native)
        history = self._history[key]
        now = self.sched.now
        t1, v1 = history[-1]
        # How far past the last real sample to go: an explicit limit, else 1.5 source periods
        # (enough to bridge the gap to the next expected sample, with a little margin).
        limit = self.max_extrapolation_s
        if limit is None:
            limit = 1.5 * (t1 - history[0][0]) if len(history) == 2 else 0.0
        if need is None or now - t1 > limit:
            return  # too long since a real sample: stop, so a dead source is not masked
        value = v1  # integer codes, enums and booleans are held
        if need.representation.encoding.startswith("float"):
            t0, v0 = history[0]
            if len(history) < 2 or t1 <= t0:
                return
            value = v1 + (v1 - v0) / (t1 - t0) * (now - t1)
        self.estimates += 1
        self._ports[subsystem].send(native, Datum(native, value, now, True, t1))

    def deliver_unavailable(self, subsystem: str, native: str, reason: str) -> None:
        self._ports[subsystem].send_status(("unavailable", native, reason))

    def announce(self, subsystems: list[str], kind: str, detail: str) -> None:
        for name in subsystems:
            self._ports[name].send_status(("annunciation", kind, detail))
