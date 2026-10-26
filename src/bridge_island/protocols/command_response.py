"""Generic command/response bus: one bus controller (BC), many remote terminals (RT).

Only the BC starts transfers; an RT can only answer when commanded. The BC runs a cyclic
schedule of minor frames, and a major frame is a fixed number of minor frames.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message


class BusError(Exception):
    pass


@dataclass
class Transfer:
    """One scheduled transfer, run in minor frames where ``frame % every == offset``."""

    rt: int
    subaddress: int
    direction: Literal["bc_to_rt", "rt_to_bc"]
    every: int = 1
    offset: int = 0
    provide: Callable[[], Message | None] | None = None  # bc_to_rt: what the BC sends
    consume: Callable[[Message | None], None] | None = None  # rt_to_bc: what the BC got


class RemoteTerminal:
    def __init__(self, name: str) -> None:
        self.name = name
        self.address: int | None = None
        self.on_receive: dict[int, Callable[[Message], None]] = {}
        self.on_transmit: dict[int, Callable[[], Message | None]] = {}


class BusController:
    def __init__(self, name: str) -> None:
        self.name = name
        self.transfers: list[Transfer] = []


class CommandResponseBus:
    def __init__(
        self,
        sched: Scheduler,
        minor_hz: float = 100.0,
        minor_per_major: int = 100,
        latency_s: float = 0.0005,
    ) -> None:
        self.sched = sched
        self.minor_hz = minor_hz
        self.minor_per_major = minor_per_major
        self.latency_s = latency_s
        self.controller: BusController | None = None
        self.terminals: dict[int, RemoteTerminal] = {}
        self.errors: list[str] = []
        self.transfers_done = 0

    def every_for(self, rate_hz: float) -> int:
        """Minor frames between transfers for a rate; the rate must divide the minor rate."""
        ratio = self.minor_hz / rate_hz
        if abs(ratio - round(ratio)) > 1e-9 or ratio < 1:
            raise BusError(f"{rate_hz} Hz does not divide the {self.minor_hz} Hz minor frame")
        return round(ratio)

    def set_controller(self, bc: object) -> None:
        if not isinstance(bc, BusController):
            raise BusError("only a BusController can control the bus; a remote terminal cannot")
        if self.controller is not None:
            raise BusError("bus already has a controller")
        self.controller = bc

    def attach(self, rt: RemoteTerminal) -> int:
        if len(self.terminals) >= 30:
            raise BusError("a bus has at most 30 remote terminals")
        rt.address = len(self.terminals) + 1
        self.terminals[rt.address] = rt
        return rt.address

    def start(self) -> None:
        if self.controller is None:
            raise BusError("no bus controller")
        frame = [0]

        def minor() -> None:
            assert self.controller is not None
            for t in self.controller.transfers:
                if frame[0] % t.every == t.offset:
                    self._run(t)
            frame[0] += 1

        self.sched.every(1.0 / self.minor_hz, minor)

    def _run(self, t: Transfer) -> None:
        rt = self.terminals.get(t.rt)
        if rt is None:
            self.errors.append(f"no response from RT {t.rt}")
            return
        self.transfers_done += 1
        if t.direction == "bc_to_rt":
            msg = t.provide() if t.provide else None
            handler = rt.on_receive.get(t.subaddress)
            if msg is None:
                return
            if handler is None:
                self.errors.append(f"RT {t.rt} has no receive subaddress {t.subaddress}")
                return
            self.sched.after(self.latency_s, lambda: handler(msg))
        else:
            source = rt.on_transmit.get(t.subaddress)
            if source is None:
                self.errors.append(f"RT {t.rt} has no transmit subaddress {t.subaddress}")
                return
            reply = source()
            consume = t.consume
            if consume:
                self.sched.after(self.latency_s, lambda: consume(reply))
