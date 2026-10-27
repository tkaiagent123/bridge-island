"""Assemble a runnable system from an example's pieces: bridged, or point to point."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from bridge_island.baseline.platform import BaselineInterface, BaselinePlatform, Source
from bridge_island.bridge import Bridge
from bridge_island.core.clock import Scheduler
from bridge_island.core.links import Network
from bridge_island.p2p.direct import DirectAdapter, attach_direct
from bridge_island.unique.subsystem import UniqueSubsystem

SubsystemFactory = Callable[[Scheduler], dict[str, UniqueSubsystem]]


@dataclass
class System:
    sched: Scheduler
    platform: BaselinePlatform
    bridge: Bridge | None
    subsystems: dict[str, UniqueSubsystem]
    network: Network
    adapters: dict[str, DirectAdapter] | None = None  # P2P only

    def run(self, seconds: float) -> None:
        self.sched.run_until(seconds)


def build_bridged(
    interface: BaselineInterface,
    sources: Mapping[str, Source],
    mapping: ModuleType,
    make_subsystems: SubsystemFactory,
    seed: int = 1,
    name: str = "baseline",
    **bridge_opts: Any,
) -> System:
    """The baseline, one Bridge and the unique systems, started."""
    sched = Scheduler(seed)
    network = Network()
    platform = BaselinePlatform(sched, interface, sources, name=name)
    bridge = Bridge(sched, interface, mapping, network, **bridge_opts)
    subs = make_subsystems(sched)
    bridge.attach_baseline(platform)
    for sub in subs.values():
        bridge.attach_unique(sub)
    bridge.start()
    platform.start()
    return System(sched, platform, bridge, subs, network)


def build_direct(
    interface: BaselineInterface,
    sources: Mapping[str, Source],
    mapping: ModuleType,
    adapter_interface: BaselineInterface,
    make_subsystems: SubsystemFactory,
    seed: int = 1,
    name: str = "baseline",
) -> System:
    """The same unique systems wired point to point, each with its own baseline adapter."""
    sched = Scheduler(seed)
    network = Network()
    platform = BaselinePlatform(sched, interface, sources, name=name, enforce_bridge=False)
    network.add(platform)
    subs = make_subsystems(sched)
    adapters = {
        n: attach_direct(platform, s, mapping, adapter_interface, network) for n, s in subs.items()
    }
    platform.start()
    return System(sched, platform, None, subs, network, adapters)
