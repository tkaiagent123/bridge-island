"""Health monitor: detects stale data and stall-and-resume discontinuities, and annunciates them."""

from __future__ import annotations

from collections.abc import Callable

from bridge_island.baseline.platform import BaselineInterface
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message
from bridge_island.core.events import EventLog


class HealthMonitor:
    """``announce(message_name, kind, detail)`` tells the affected unique systems."""

    def __init__(
        self,
        sched: Scheduler,
        log: EventLog,
        interface: BaselineInterface,
        announce: Callable[[str, str, str], None],
        check_period_s: float = 0.1,
        stale_factor: float = 3.0,
        gap_factor: float = 3.0,
    ) -> None:
        self.sched = sched
        self.log = log
        self.interface = interface
        self.announce = announce
        self.check_period_s = check_period_s
        self.stale_factor = stale_factor
        self.gap_factor = gap_factor
        self._last_source: dict[str, float] = {}
        self._last_arrival: dict[str, float] = {}
        self._stale: set[str] = set()
        self._last_scan: float | None = None

    def observe(self, msg: Message) -> None:
        spec = self.interface.published_message(msg.spec_name)
        if spec is None:
            return
        prev = self._last_source.get(msg.spec_name)
        if prev is not None:
            gap = msg.source_time - prev
            if gap > self.gap_factor / spec.rate_hz:
                self._event("discontinuity", msg.spec_name, f"source time jumped {gap:.3f} s")
            elif gap < 0:
                self._event("out_of_order", msg.spec_name, f"source time went back {-gap:.3f} s")
        self._last_source[msg.spec_name] = msg.source_time
        self._last_arrival[msg.spec_name] = self.sched.now
        if msg.spec_name in self._stale:
            self._stale.discard(msg.spec_name)
            self._event("recovered", msg.spec_name, "data flowing again")

    def scan(self) -> None:
        """Periodic check. The Bridge calls it only while running, so a stall leaves a gap."""
        now = self.sched.now
        if self._last_scan is not None and now - self._last_scan > 2 * self.check_period_s:
            self._event(
                "bridge_stall", "bridge", f"no health scan for {now - self._last_scan:.3f} s"
            )
        self._last_scan = now
        for spec in self.interface.published:
            age = now - self._last_arrival.get(spec.name, 0.0)
            if age > self.stale_factor / spec.rate_hz and spec.name not in self._stale:
                self._stale.add(spec.name)
                self._event("stale", spec.name, f"no data for {age:.3f} s")

    def _event(self, kind: str, subject: str, detail: str) -> None:
        self.log.add(self.sched.now, "health", f"health.{kind}", f"{subject}: {detail}")
        self.announce(subject, kind, f"{subject}: {detail}")  # say which message it is about
