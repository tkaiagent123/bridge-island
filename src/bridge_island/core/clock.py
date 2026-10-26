"""Deterministic simulated clock and discrete-event scheduler. No wall-clock time."""

from __future__ import annotations

import heapq
import random
from collections.abc import Callable


class Scheduler:
    """Runs callbacks in simulated-time order. Ties run in scheduling order."""

    def __init__(self, seed: int = 0) -> None:
        self.now = 0.0
        self.rng = random.Random(seed)
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = 0

    def at(self, time: float, fn: Callable[[], None]) -> None:
        time = round(time, 9)  # keeps k * period free of float drift
        if time < self.now:
            raise ValueError(f"cannot schedule at {time} before now={self.now}")
        heapq.heappush(self._queue, (time, self._seq, fn))
        self._seq += 1

    def after(self, delay: float, fn: Callable[[], None]) -> None:
        self.at(self.now + delay, fn)

    def every(self, period: float, fn: Callable[[], None], start: float = 0.0) -> None:
        """Call ``fn`` at ``start + k * period`` for k = 0, 1, 2, ..."""

        def tick(k: int) -> None:
            fn()
            self.at(start + (k + 1) * period, lambda: tick(k + 1))

        self.at(start, lambda: tick(0))

    def run_until(self, end: float) -> None:
        while self._queue and self._queue[0][0] <= end:
            time, _, fn = heapq.heappop(self._queue)
            self.now = time
            fn()
        self.now = max(self.now, end)
