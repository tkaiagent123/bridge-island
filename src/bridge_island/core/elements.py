"""Data elements, representations, messages and time-tagged data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Representation:
    """How an element is carried: encoding, units, frame and protocol."""

    encoding: str
    units: str
    frame: str
    protocol: str

    def same_value_form(self, other: Representation) -> bool:
        """True if the value needs no conversion (protocol differences are the UID's job)."""
        return (self.encoding, self.units, self.frame) == (
            other.encoding,
            other.units,
            other.frame,
        )


@dataclass(frozen=True)
class DataElement:
    name: str
    representation: Representation
    valid_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class MessageSpec:
    """A baseline message: a group of elements sharing one source time and one rate."""

    name: str
    elements: tuple[DataElement, ...]
    rate_hz: float
    subaddress: int

    def element(self, name: str) -> DataElement | None:
        return next((e for e in self.elements if e.name == name), None)

    def refs(self) -> tuple[str, ...]:
        return tuple(f"{self.name}.{e.name}" for e in self.elements)


@dataclass(frozen=True)
class Message:
    """Field values of one message, valid at ``source_time`` (seconds, simulated clock)."""

    spec_name: str
    values: Mapping[str, Any]
    source_time: float


@dataclass(frozen=True)
class Datum:
    """One element value with its *source* time of validity (G3), never delivery time."""

    element: str
    value: Any
    source_time: float

    def age(self, now: float) -> float:
        return now - self.source_time
