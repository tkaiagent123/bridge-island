"""Unique Island subsystems. Each speaks its own native protocol, units and rates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Message, Representation


@dataclass(frozen=True)
class Need:
    """An input the subsystem consumes, in its own native representation.

    ``rate_hz`` is the highest rate it can use: the Bridge never delivers faster, and
    never invents samples when the baseline is slower.
    """

    native_name: str
    representation: Representation
    rate_hz: float
    latency_budget_s: float  # max data age at the moment of delivery
    stale_after_s: float  # max data age at the moment of use


@dataclass(frozen=True)
class Output:
    """A message the subsystem may send toward the baseline (through the Bridge)."""

    message_name: str
    fields: tuple[tuple[str, Representation], ...]


@dataclass
class Received:
    native_name: str
    datum: Datum
    delivered_at: float

    @property
    def age(self) -> float:
        return self.datum.age(self.delivered_at)


@dataclass
class UniqueSubsystem:
    """A mission system. It reads data age itself: it never trusts delivery time (G3)."""

    sched: Scheduler
    name: str
    protocol: str  # "pubsub" | "command_response" | "label_words"
    needs: tuple[Need, ...]
    outputs: tuple[Output, ...] = ()
    bus_latency_s: float | None = None
    island: str = field(default="unique", init=False)
    received: list[Received] = field(default_factory=list, init=False)
    late: list[Received] = field(default_factory=list, init=False)
    unavailable: dict[str, str] = field(default_factory=dict, init=False)
    annunciations: list[tuple[float, str, str]] = field(default_factory=list, init=False)
    _uplink: Callable[[Message], None] | None = field(default=None, init=False, repr=False)

    def need(self, native_name: str) -> Need | None:
        return next((n for n in self.needs if n.native_name == native_name), None)

    def on_data(self, native_name: str, datum: Datum) -> None:
        rec = Received(native_name, datum, self.sched.now)
        self.received.append(rec)
        need = self.need(native_name)
        if need and rec.age > need.latency_budget_s:
            self.late.append(rec)

    def on_unavailable(self, native_name: str, reason: str) -> None:
        self.unavailable[native_name] = reason

    def on_annunciation(self, kind: str, detail: str) -> None:
        self.annunciations.append((self.sched.now, kind, detail))

    def latest(self, native_name: str) -> Received | None:
        return next((r for r in reversed(self.received) if r.native_name == native_name), None)

    def check(self) -> list[str]:
        """Consumer-side checks at the current time: missing, unavailable or stale inputs."""
        issues = []
        for need in self.needs:
            if need.native_name in self.unavailable:
                issues.append(
                    f"{need.native_name}: unavailable ({self.unavailable[need.native_name]})"
                )
                continue
            last = self.latest(need.native_name)
            if last is None:
                issues.append(f"{need.native_name}: no data")
            elif last.datum.age(self.sched.now) > need.stale_after_s:
                issues.append(f"{need.native_name}: stale")
        return issues

    def send(self, message_name: str, values: Mapping[str, Any]) -> None:
        """Send a message toward the baseline. The Bridge, not the sender, validates it."""
        if self._uplink is None:
            raise RuntimeError(f"{self.name} is not connected to a Bridge")
        self._uplink(Message(message_name, dict(values), self.sched.now))
