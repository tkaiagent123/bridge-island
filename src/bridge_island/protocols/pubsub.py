"""Generic topic-based publish/subscribe bus (Ethernet-style, higher latency)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from bridge_island.core.clock import Scheduler


class PubSubBus:
    def __init__(self, sched: Scheduler, latency_s: float = 0.002) -> None:
        self.sched = sched
        self.latency_s = latency_s
        self.published = 0
        self._subs: defaultdict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, topic: str, fn: Callable[[Any], None]) -> None:
        self._subs[topic].append(fn)

    def publish(self, topic: str, payload: Any) -> None:
        self.published += 1
        for fn in self._subs[topic]:
            self.sched.after(self.latency_s, lambda fn=fn: fn(payload))  # type: ignore[misc]
