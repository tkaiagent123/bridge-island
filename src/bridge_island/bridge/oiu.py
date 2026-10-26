"""OEM Interface Unit (OIU): the Bridge part facing the baseline.

It obeys the baseline's published interface exactly and acts as a remote terminal (RT):
it can only answer when the baseline's bus controller commands it (G4).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from bridge_island.baseline.platform import BaselineInterface
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message, MessageSpec
from bridge_island.core.events import EventLog
from bridge_island.protocols.command_response import RemoteTerminal


class OemInterfaceUnit:
    island = "bridge"

    def __init__(
        self,
        name: str,
        interface: BaselineInterface,
        sched: Scheduler,
        log: EventLog,
        on_message: Callable[[Message], None],
    ) -> None:
        self.name = name
        self.interface = interface
        self.sched = sched
        self.log = log
        self.on_message = on_message
        self.rt = RemoteTerminal(name)
        self.rejected_in = 0
        self.rejected_out = 0
        self._queues: dict[str, deque[Message]] = {}
        for spec in interface.published:
            self.rt.on_receive[spec.subaddress] = self._receiver(spec)
        for spec in interface.accepted:
            self._queues[spec.name] = deque()
            self.rt.on_transmit[spec.subaddress] = self._transmitter(spec)

    def _receiver(self, spec: MessageSpec) -> Callable[[Message], None]:
        names = {e.name for e in spec.elements}

        def receive(msg: Message) -> None:
            if msg.spec_name != spec.name or not set(msg.values) <= names:
                self.rejected_in += 1
                self.log.add(
                    self.sched.now, self.name, "oiu.reject_in", f"{msg.spec_name} not as published"
                )
                return
            self.on_message(msg)

        return receive

    def _transmitter(self, spec: MessageSpec) -> Callable[[], Message | None]:
        queue = self._queues[spec.name]
        return lambda: queue.popleft() if queue else None

    def submit(self, msg: Message) -> bool:
        """Queue a message for the baseline. Only specified accepted messages get through."""
        spec = self.interface.accepted_message(msg.spec_name)
        if spec is None or set(msg.values) != {e.name for e in spec.elements}:
            self.rejected_out += 1
            self.log.add(
                self.sched.now,
                self.name,
                "oiu.reject_out",
                f"{msg.spec_name} not accepted by baseline",
            )
            return False
        self._queues[spec.name].append(msg)
        return True
