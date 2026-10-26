"""The Bridge: one object composing the OIU, TM, UID and the two monitors (G6).

Mixed criticality is handled by internal separation, not by spreading the Bridge across
several objects: each component sits in its own partition and talks to the others only
through callbacks owned by the Bridge, never through direct references.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from bridge_island.baseline.platform import BaselineInterface, BaselinePlatform
from bridge_island.bridge.cyber import CyberMonitor
from bridge_island.bridge.health import HealthMonitor
from bridge_island.bridge.oiu import OemInterfaceUnit
from bridge_island.bridge.translator import MappingModule, TranslatorModule
from bridge_island.bridge.uid import UniqueInterfaceDistributor
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message
from bridge_island.core.events import EventLog
from bridge_island.core.links import Network
from bridge_island.core.transforms import TransformError
from bridge_island.unique.subsystem import UniqueSubsystem


class InterfaceMismatch(Exception):
    """The mapping does not fit the baseline interface it is built against."""


class Bridge:
    island = "bridge"
    PARTITIONS: ClassVar[dict[str, str]] = {
        "oiu": "baseline-facing",
        "tm": "translation",
        "uid": "unique-facing",
        "health": "monitor",
        "cyber": "monitor",
    }

    def __init__(
        self,
        sched: Scheduler,
        interface: BaselineInterface,
        mapping: MappingModule,
        network: Network | None = None,
        time_tag: Literal["source", "delivery"] = "source",
        processing_delay_s: float = 0.001,
        name: str = "bridge",
    ) -> None:
        self.name = name
        self.sched = sched
        self.interface = interface
        self.mapping = mapping
        self.network = network
        self.processing_delay_s = processing_delay_s
        self.stalled = False
        self._held: dict[str, Message] = {}
        self._check_fit()
        self.log = EventLog()
        self.oiu = OemInterfaceUnit(f"{name}.oiu", interface, sched, self.log, self._on_baseline)
        self.tm = TranslatorModule(mapping, sched, self.log, time_tag)
        self.uid = UniqueInterfaceDistributor(f"{name}.uid", sched, self.log, self._on_unique)
        self.cyber = CyberMonitor(
            sched,
            self.log,
            ((ref, sub) for ref, sub, _, _ in mapping.DOWNLINKS),
            ((sub, msg) for sub, msg, _ in mapping.UPLINKS),
        )
        self.health = HealthMonitor(sched, self.log, interface, self._announce)
        if network is not None:
            network.add(self)

    def _check_fit(self) -> None:
        """G4: consume only what the baseline publishes, emit only what it accepts."""
        m = self.mapping
        problems = []
        if self.interface.version != m.BASELINE_VERSION:
            problems.append(
                f"mapping is for baseline v{m.BASELINE_VERSION}, not v{self.interface.version}"
            )
        problems += [
            f"{ref} is not published by baseline v{self.interface.version}"
            for ref, _, _, _ in m.DOWNLINKS
            if self.interface.element(ref) is None
        ]
        problems += [
            f"{msg} is not accepted by baseline v{self.interface.version}"
            for _, _, msg in m.UPLINKS
            if self.interface.accepted_message(msg) is None
        ]
        if problems:
            raise InterfaceMismatch("; ".join(problems))

    # --- wiring ---------------------------------------------------------------------

    def attach_baseline(self, platform: BaselinePlatform) -> None:
        platform.connect(self.oiu.rt, owner=self)
        if self.network is not None:
            self.network.add(platform)
            self.network.connect(platform.name, self.name, "command_response")

    def attach_unique(self, sub: UniqueSubsystem) -> None:
        self.uid.connect(sub)
        if self.network is not None:
            self.network.add(sub)
            self.network.connect(sub.name, self.name, sub.protocol)

    def start(self) -> None:
        """Report removed baseline elements to the affected subsystems, and start monitoring."""
        for ref, sub, native, reason in self.mapping.UNAVAILABLE:
            self.log.add(
                self.sched.now,
                self.name,
                "bridge.unavailable",
                f"{ref} -> {sub}.{native}: {reason}",
            )
            if sub in self.uid.subsystems:
                self.uid.deliver_unavailable(sub, native, reason)
        period = self.health.check_period_s
        self.sched.every(period, self._scan_if_running, start=period)

    def _scan_if_running(self) -> None:
        if not self.stalled:
            self.health.scan()

    def stall(self, at: float, duration: float) -> None:
        """Fault injection: the Bridge stops processing at ``at`` and resumes after ``duration``."""
        self.sched.at(at, self._begin_stall)
        self.sched.at(at + duration, self._end_stall)

    def _begin_stall(self) -> None:
        self.stalled = True
        self.log.add(self.sched.now, self.name, "bridge.stall", "processing stopped")

    def _end_stall(self) -> None:
        self.stalled = False
        self.log.add(self.sched.now, self.name, "bridge.resume", "processing resumed")
        held, self._held = self._held, {}
        for msg in held.values():  # the RT buffer keeps only the latest message per subaddress
            self._on_baseline(msg)

    # --- data paths -----------------------------------------------------------------

    def _hold(self, msg: Message) -> None:
        """While stalled, keep only the latest message per name, like an RT subaddress buffer."""
        held = self._held.get(msg.spec_name)
        if held is None or msg.source_time >= held.source_time:
            self._held[msg.spec_name] = msg

    def _on_baseline(self, msg: Message) -> None:
        if self.stalled:
            self._hold(msg)
            return
        self.health.observe(msg)
        self.sched.after(self.processing_delay_s, lambda: self._forward(msg))

    def _forward(self, msg: Message) -> None:
        if self.stalled:  # in flight when the stall began: released, still old, on resume
            self._hold(msg)
            return
        for d in self.tm.down(msg):
            if self.cyber.check_down(d.ref, d.subsystem):
                self.uid.deliver(d.subsystem, d.datum)

    def _on_unique(self, sender: str, msg: Message) -> None:
        if self.stalled:
            self.log.add(
                self.sched.now,
                self.name,
                "bridge.drop_up",
                f"{sender}.{msg.spec_name} while stalled",
            )
            return
        if not self.cyber.check_up(sender, msg.spec_name):
            return
        try:
            out = self.tm.up(sender, msg)
        except TransformError as exc:
            self.tm.rejected += 1
            self.log.add(self.sched.now, "tm", "tm.reject_up", f"{sender}.{msg.spec_name}: {exc}")
            return
        self.oiu.submit(out)

    def _announce(self, subject: str, kind: str, detail: str) -> None:
        """Tell the unique systems that depend on ``subject`` (a message, or the whole Bridge)."""
        if subject == "bridge":
            names = list(self.uid.subsystems)
        else:
            names = sorted(
                {s for ref, s, _, _ in self.mapping.DOWNLINKS if ref.split(".")[0] == subject}
            )
        self.uid.announce([n for n in names if n in self.uid.subsystems], kind, detail)
