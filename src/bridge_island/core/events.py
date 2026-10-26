"""Append-only event log shared by the Bridge components."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    time: float
    source: str
    kind: str
    detail: str


@dataclass
class EventLog:
    events: list[Event] = field(default_factory=list)

    def add(self, time: float, source: str, kind: str, detail: str) -> None:
        self.events.append(Event(time, source, kind, detail))

    def of_kind(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]
