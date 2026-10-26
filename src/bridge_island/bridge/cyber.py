"""Cyber monitor: enforces which data may cross the Bridge, and in which direction."""

from __future__ import annotations

from collections.abc import Iterable

from bridge_island.core.clock import Scheduler
from bridge_island.core.events import EventLog


class CyberMonitor:
    def __init__(
        self,
        sched: Scheduler,
        log: EventLog,
        allowed_down: Iterable[tuple[str, str]],
        allowed_up: Iterable[tuple[str, str]],
    ) -> None:
        """``allowed_down``: (baseline element, subsystem). ``allowed_up``: (subsystem, message)."""
        self.sched = sched
        self.log = log
        self.allowed_down = frozenset(allowed_down)
        self.allowed_up = frozenset(allowed_up)
        self.blocked = 0

    def check_down(self, ref: str, subsystem: str) -> bool:
        if (ref, subsystem) in self.allowed_down:
            return True
        self._block("down", f"{ref} -> {subsystem}")
        return False

    def check_up(self, subsystem: str, message: str) -> bool:
        if (subsystem, message) in self.allowed_up:
            return True
        self._block("up", f"{subsystem} -> {message}")
        return False

    def _block(self, direction: str, detail: str) -> None:
        self.blocked += 1
        self.log.add(self.sched.now, "cyber", "cyber.block", f"{direction}: {detail}")
