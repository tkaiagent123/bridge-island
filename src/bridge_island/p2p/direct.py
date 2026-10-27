"""Point-to-point (P2P) integration: each unique system wired straight to the baseline.

The conversions are the same ones the Bridge applies. The difference is where they live:
inside every subsystem's own adapter, which sits on the baseline bus as its own remote
terminal. Nothing validates what a subsystem sends, and a baseline change is felt by every
adapter that reads the changed element.
"""

from __future__ import annotations

from collections import deque
from types import ModuleType
from typing import Any

from bridge_island.baseline.platform import BaselineInterface, BaselinePlatform
from bridge_island.core.elements import Datum, Message
from bridge_island.core.links import Network
from bridge_island.core.transforms import TransformError
from bridge_island.protocols.command_response import RemoteTerminal
from bridge_island.unique.subsystem import UniqueSubsystem


class DirectAdapter:
    """A subsystem's own baseline adapter. ``mapping`` is the generated module it was written
    against, and ``interface`` the baseline version that module targets."""

    def __init__(
        self,
        platform: BaselinePlatform,
        sub: UniqueSubsystem,
        mapping: ModuleType,
        interface: BaselineInterface,
    ) -> None:
        self.sub, self.mapping = sub, mapping
        self.rt = RemoteTerminal(sub.name)
        self.errors: list[tuple[float, str, str]] = []
        self.uplink_queue: deque[Message] = deque()
        self._last_sent: dict[str, float] = {}
        self._refs = {ref for ref, s, _, _ in mapping.DOWNLINKS if s == sub.name}
        for message in {ref.split(".")[0] for ref in self._refs}:
            spec = interface.published_message(message)
            if spec is not None:
                self.rt.on_receive[spec.subaddress] = self._on_message
        for _, s, native, reason in mapping.UNAVAILABLE:
            if s == sub.name:  # the subsystem's own developers had to add this handling
                sub.on_unavailable(native, reason)
        if sub.outputs:
            sub._uplink = self._send
        for spec in interface.accepted:  # every terminal answers the baseline's polls
            self.rt.on_transmit[spec.subaddress] = self._next_uplink
        platform.connect(self.rt, owner=sub)

    def _on_message(self, msg: Message) -> None:
        now = self.sub.sched.now
        outs, errors = self.mapping.translate(msg.spec_name, msg.values)
        for ref, why in errors:
            if ref in self._refs:
                self.errors.append((now, ref, why))
        for _, s, native, value, _ in outs:
            need = self.sub.need(native)
            if s != self.sub.name or need is None:
                continue
            last = self._last_sent.get(native)
            if last is not None and now - last < 1.0 / need.rate_hz - 1e-9:
                continue
            self._last_sent[native] = now
            self.sub.on_data(native, Datum(native, value, msg.source_time))

    def _send(self, msg: Message) -> None:
        try:
            name, values = self.mapping.translate_up(self.sub.name, msg.spec_name, msg.values)
        except TransformError as exc:  # all the protection a P2P subsystem has is its own code
            self.errors.append((self.sub.sched.now, msg.spec_name, str(exc)))
            return
        self.uplink_queue.append(Message(name, values, msg.source_time))

    def send_raw(self, name: str, values: dict[str, Any]) -> None:
        """What faulty subsystem software can do in P2P: put anything on the baseline bus."""
        self.uplink_queue.append(Message(name, values, self.sub.sched.now))

    def _next_uplink(self) -> Message | None:
        return self.uplink_queue.popleft() if self.uplink_queue else None


def attach_direct(
    platform: BaselinePlatform,
    sub: UniqueSubsystem,
    mapping: ModuleType,
    interface: BaselineInterface,
    network: Network | None = None,
) -> DirectAdapter:
    adapter = DirectAdapter(platform, sub, mapping, interface)
    if network is not None:
        network.add(sub)
        network.connect(sub.name, platform.name, "command_response")
    return adapter
