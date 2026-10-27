"""Generic label-word bus: one transmitter broadcasts labelled words to many receivers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bridge_island.core.clock import Scheduler


class LabelBusError(Exception):
    pass


@dataclass(frozen=True)
class Word:
    label: int
    value: Any
    source_time: float
    estimated: bool = False
    basis_time: float | None = None


class LabelBus:
    def __init__(self, sched: Scheduler, transmitter: str, latency_s: float = 0.0002) -> None:
        self.sched = sched
        self.transmitter = transmitter
        self.latency_s = latency_s
        self.words_sent = 0
        self._subs: defaultdict[int, list[Callable[[Word], None]]] = defaultdict(list)

    def subscribe(self, label: int, fn: Callable[[Word], None]) -> None:
        self._subs[label].append(fn)

    def transmit(self, by: str, word: Word) -> None:
        if by != self.transmitter:
            raise LabelBusError(f"{by!r} is not the transmitter on this bus")
        self.words_sent += 1
        for fn in self._subs[word.label]:
            self.sched.after(self.latency_s, lambda fn=fn: fn(word))  # type: ignore[misc]
