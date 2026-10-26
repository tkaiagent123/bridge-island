"""Translator Module (TM): all format, content, protocol-neutral conversion and time tags.

The mapping is a generated module (G5); the TM only calls it. Time tags follow G3: each
datum keeps the source time of validity. ``time_tag="delivery"`` is the deliberately naive
mode used to demonstrate the error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Message
from bridge_island.core.events import EventLog


class MappingModule(Protocol):
    """What a generated mapping module provides."""

    SPEC_NAME: str
    SPEC_SHA256: str
    BASELINE_VERSION: int
    # (baseline element, subsystem, native name, identity)
    DOWNLINKS: tuple[tuple[str, str, str, bool], ...]
    # (baseline element, subsystem, native name, reason)
    UNAVAILABLE: tuple[tuple[str, str, str, str], ...]
    # (subsystem, unique message, baseline message)
    UPLINKS: tuple[tuple[str, str, str], ...]
    translate: Callable[
        [str, Mapping[str, Any]],
        tuple[list[tuple[str, str, str, Any, bool]], list[tuple[str, str]]],
    ]
    translate_up: Callable[[str, str, Mapping[str, Any]], tuple[str, dict[str, Any]]]


@dataclass(frozen=True)
class Delivery:
    ref: str  # baseline element it came from
    subsystem: str
    datum: Datum  # in the subsystem's native representation


class TranslatorModule:
    def __init__(
        self,
        mapping: MappingModule,
        sched: Scheduler,
        log: EventLog,
        time_tag: Literal["source", "delivery"] = "source",
    ) -> None:
        self.mapping = mapping
        self.sched = sched
        self.log = log
        self.time_tag = time_tag
        self.identity_count = 0
        self.translated_count = 0
        self.rejected = 0

    def down(self, msg: Message) -> list[Delivery]:
        outs, errors = self.mapping.translate(msg.spec_name, msg.values)
        for ref, why in errors:
            self.rejected += 1
            self.log.add(self.sched.now, "tm", "tm.reject", f"{ref}: {why}")
        tag = msg.source_time if self.time_tag == "source" else self.sched.now
        result = []
        for ref, sub, native, value, identity in outs:
            if identity:
                self.identity_count += 1
            else:
                self.translated_count += 1
            result.append(Delivery(ref, sub, Datum(native, value, tag)))
        return result

    def up(self, sender: str, msg: Message) -> Message:
        """Translate a unique message to a baseline message. Raises TransformError if bad."""
        name, values = self.mapping.translate_up(sender, msg.spec_name, msg.values)
        return Message(name, values, msg.source_time)
