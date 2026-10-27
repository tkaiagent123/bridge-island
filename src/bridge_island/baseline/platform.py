"""Baseline Island: the host platform. Its interface is versioned and fixed once built."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import DataElement, Message, MessageSpec
from bridge_island.core.links import BypassError, IslandMember
from bridge_island.protocols.command_response import (
    BusController,
    CommandResponseBus,
    RemoteTerminal,
    Transfer,
)


@dataclass(frozen=True)
class BaselineInterface:
    """The published interface of one baseline version."""

    version: int
    published: tuple[MessageSpec, ...]
    accepted: tuple[MessageSpec, ...]

    def published_message(self, name: str) -> MessageSpec | None:
        return next((m for m in self.published if m.name == name), None)

    def accepted_message(self, name: str) -> MessageSpec | None:
        return next((m for m in self.accepted if m.name == name), None)

    def element(self, ref: str) -> DataElement | None:
        """Look up a published element by ``Message.field``."""
        msg, _, fld = ref.partition(".")
        spec = self.published_message(msg)
        return spec.element(fld) if spec else None


Source = Callable[[float], Mapping[str, Any]]


@dataclass
class BaselinePlatform:
    """Emits its published messages at their rates; accepts only its specified inputs.

    The platform is the bus controller of its own bus. Only a Bridge may attach to it
    (G1) unless ``enforce_bridge`` is switched off to model point-to-point integration.
    """

    sched: Scheduler
    interface: BaselineInterface
    sources: Mapping[str, Source]
    name: str = "baseline"
    sample_age_s: float = 0.005
    enforce_bridge: bool = True
    island: str = field(default="baseline", init=False)
    accepted: list[tuple[float, Message]] = field(default_factory=list, init=False)
    rejected: list[tuple[float, str]] = field(default_factory=list, init=False)
    emitted: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.bus = CommandResponseBus(self.sched)
        self._bc = BusController(self.name)
        self.bus.set_controller(self._bc)
        self._started = False

    def connect(self, rt: RemoteTerminal, owner: IslandMember) -> int:
        """Attach a remote terminal. Returns its bus address."""
        if self.enforce_bridge and owner.island != "bridge":
            raise BypassError(f"{owner.name!r} ({owner.island}) may not attach to the baseline")
        return self.bus.attach(rt)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for rt_addr in self.bus.terminals:
            rt = self.bus.terminals[rt_addr]
            for spec in self.interface.published:
                if spec.subaddress not in rt.on_receive:
                    continue  # this terminal does not take that message
                self._bc.transfers.append(
                    Transfer(
                        rt_addr,
                        spec.subaddress,
                        "bc_to_rt",
                        every=self.bus.every_for(spec.rate_hz),
                        provide=lambda spec=spec: self._emit(spec),  # type: ignore[misc]
                    )
                )
            for spec in self.interface.accepted:
                if spec.subaddress not in rt.on_transmit:
                    continue
                self._bc.transfers.append(
                    Transfer(
                        rt_addr,
                        spec.subaddress,
                        "rt_to_bc",
                        every=self.bus.every_for(spec.rate_hz),
                        consume=self._accept,
                    )
                )
        self.bus.start()

    def _emit(self, spec: MessageSpec) -> Message:
        valid_at = round(self.sched.now - self.sample_age_s, 9)
        self.emitted[spec.name] = self.emitted.get(spec.name, 0) + 1
        return Message(spec.name, dict(self.sources[spec.name](valid_at)), valid_at)

    def _accept(self, msg: Message | None) -> None:
        if msg is None:
            return
        now = self.sched.now
        spec = self.interface.accepted_message(msg.spec_name)
        if spec is None:
            self.rejected.append((now, f"{msg.spec_name} is not an accepted input"))
        elif set(msg.values) != {e.name for e in spec.elements}:
            self.rejected.append((now, f"{msg.spec_name} has the wrong fields"))
        else:
            for e in spec.elements:
                v = msg.values[e.name]
                if e.valid_range and not (
                    isinstance(v, (int, float)) and e.valid_range[0] <= v <= e.valid_range[1]
                ):
                    self.rejected.append((now, f"{msg.spec_name}.{e.name}={v!r} out of range"))
                    return
            self.accepted.append((now, msg))
