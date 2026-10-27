"""Generic Host Air Vehicle (GHAV): an invented aviation example.

Every platform, message, field, rate and number here is invented.

Baseline interface v2 applies this change set to v1:
  * rename:    NavigationState.groundspeed_kt -> ground_speed_kt
  * unit:      NavigationState.altitude feet -> meters
  * encoding:  NavigationState.heading 16-bit binary angle -> float degrees
  * rate:      AttitudeState 50 Hz -> 100 Hz
  * added:     NavigationState.vertical_speed_mps
  * removed:   AirData.baro_altitude_ft
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge_island.baseline.platform import BaselineInterface, BaselinePlatform
from bridge_island.bridge import Bridge
from bridge_island.codegen import Dictionary, load_module
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import DataElement, MessageSpec, Representation
from bridge_island.core.links import Network
from bridge_island.unique.subsystem import Need, Output, UniqueSubsystem

HERE = Path(__file__).parent
CR, PS, LW = "command_response", "pubsub", "label_words"

# Renames between versions (old ref -> new ref); a diff cannot discover these on its own.
RENAMES = {"NavigationState.groundspeed_kt": "NavigationState.ground_speed_kt"}


def R(encoding: str, units: str, frame: str, protocol: str) -> Representation:
    return Representation(encoding, units, frame, protocol)


def E(name: str, rep: Representation, rng: tuple[float, float] | None = None) -> DataElement:
    return DataElement(name, rep, rng)


def f64(units: str, frame: str) -> Representation:
    return R("float64", units, frame, CR)


def interface(version: int = 1) -> BaselineInterface:
    if version not in (1, 2):
        raise ValueError(f"GHAV baseline v{version} is not defined")
    v2 = version == 2
    nav = [
        E("latitude", f64("deg", "wgs84"), (-90, 90)),
        E("longitude", f64("deg", "wgs84"), (-180, 180)),
        E("altitude", f64("m", "msl"), (-450, 18500))
        if v2
        else E("altitude", f64("ft", "msl"), (-1500, 60000)),
        E("heading", f64("deg", "true_north"), (0, 360))
        if v2
        else E("heading", R("bam16", "deg", "true_north", CR)),
        E("ground_speed_kt" if v2 else "groundspeed_kt", f64("kt", "ground"), (0, 1500)),
    ]
    if v2:
        nav.append(E("vertical_speed_mps", f64("m/s", "ground"), (-100, 100)))
    air = [E("airspeed_kt", f64("kt", "air"), (0, 1500))]
    if not v2:
        air.insert(0, E("baro_altitude_ft", f64("ft", "msl"), (-1500, 60000)))
    return BaselineInterface(
        version=version,
        published=(
            MessageSpec("NavigationState", tuple(nav), rate_hz=20, subaddress=1),
            MessageSpec("AirData", tuple(air), rate_hz=10, subaddress=2),
            MessageSpec(
                "AttitudeState",
                (
                    E("roll_deg", f64("deg", "body"), (-180, 180)),
                    E("pitch_deg", f64("deg", "body"), (-90, 90)),
                    E("yaw_deg", f64("deg", "body"), (0, 360)),
                ),
                rate_hz=100 if v2 else 50,
                subaddress=3,
            ),
            MessageSpec(
                "PowerStatus",
                (
                    E("bus_voltage_v", f64("V", "bus"), (0, 40)),
                    E("power_state", R("uint8", "1", "bus", CR)),
                ),
                rate_hz=1,
                subaddress=4,
            ),
            MessageSpec(
                "TimeReference", (E("utc_seconds", f64("s", "utc")),), rate_hz=1, subaddress=5
            ),
        ),
        accepted=(
            MessageSpec(
                "DisplayRequest",
                (
                    E("page_id", R("uint8", "1", "display", CR), (1, 3)),
                    E("priority", R("uint8", "1", "display", CR), (0, 3)),
                ),
                rate_hz=10,
                subaddress=6,
            ),
        ),
    )


def _needs() -> dict[str, tuple[Need, ...]]:
    def n(
        name: str,
        enc: str,
        units: str,
        frame: str,
        proto: str,
        hz: float,
        budget: float,
        stale: float,
    ) -> Need:
        return Need(name, R(enc, units, frame, proto), hz, budget, stale)

    return {
        "PlanningDisplay": (
            n("nav_lat_deg", "float64", "deg", "wgs84", PS, 5, 0.5, 2.0),
            n("nav_lon_deg", "float64", "deg", "wgs84", PS, 5, 0.5, 2.0),
            n("nav_alt_m", "float64", "m", "msl", PS, 5, 0.5, 2.0),
            n("nav_heading_deg", "float64", "deg", "true_north", PS, 5, 0.5, 2.0),
            n("nav_speed_kmh", "float64", "km/h", "ground", PS, 5, 0.5, 2.0),
        ),
        "CueingProcessor": (
            n("pos_lat_rad", "float64", "rad", "wgs84", LW, 50, 0.05, 0.25),
            n("pos_lon_rad", "float64", "rad", "wgs84", LW, 50, 0.05, 0.25),
            n("pos_alt_m", "float64", "m", "msl", LW, 50, 0.05, 0.25),
            n("roll_rad", "float64", "rad", "body", LW, 50, 0.05, 0.25),
            n("pitch_rad", "float64", "rad", "body", LW, 50, 0.05, 0.25),
            n("yaw_rad", "float64", "rad", "body", LW, 50, 0.05, 0.25),
            n("baro_alt_m", "float64", "m", "msl", LW, 50, 0.05, 0.25),
        ),
        "VoiceRelay": (
            n("time_utc_s", "float64", "s", "utc", LW, 10, 0.02, 2.5),
            n("power_state", "uint8", "1", "bus", LW, 10, 0.02, 2.5),
        ),
        # Speaks the v1 baseline representation exactly: all identity under v1 (G2).
        "StatusLogger": (
            n("altitude_ft", "float64", "ft", "msl", CR, 1, 1.5, 3.0),
            n("bus_voltage_v", "float64", "V", "bus", CR, 1, 1.5, 3.0),
            n("power_state", "uint8", "1", "bus", CR, 1, 1.5, 3.0),
            n("utc_seconds", "float64", "s", "utc", CR, 1, 1.5, 3.0),
        ),
    }


_PROTOCOLS = {"PlanningDisplay": PS, "CueingProcessor": LW, "VoiceRelay": LW, "StatusLogger": CR}
_OUTPUTS = {
    "PlanningDisplay": (
        Output(
            "show_page",
            (("page", R("enum", "1", "display", PS)), ("priority", R("uint8", "1", "display", PS))),
        ),
    ),
}


def dictionary(version: int = 1) -> Dictionary:
    return Dictionary(interface(version), _needs(), _OUTPUTS)


def make_subsystems(sched: Scheduler) -> dict[str, UniqueSubsystem]:
    needs = _needs()
    return {
        name: UniqueSubsystem(sched, name, proto, needs[name], _OUTPUTS.get(name, ()))
        for name, proto in _PROTOCOLS.items()
    }


def sources(version: int = 1) -> dict[str, Any]:
    """Invented, deterministic signals, evaluated at the time of validity."""
    v2 = version == 2

    def nav(t: float) -> dict[str, Any]:
        alt_ft = 5000.0 + 100.0 * math.sin(t / 10)
        heading = (90.0 + 2.0 * t) % 360.0
        speed = 250.0 + 5.0 * math.sin(t / 7)
        if v2:
            return {
                "latitude": 41.0 + 1e-4 * t,
                "longitude": -72.0 + 1.5e-4 * t,
                "altitude": alt_ft * 0.3048,
                "heading": heading,
                "ground_speed_kt": speed,
                "vertical_speed_mps": 10.0 * 0.3048 * math.cos(t / 10),
            }
        return {
            "latitude": 41.0 + 1e-4 * t,
            "longitude": -72.0 + 1.5e-4 * t,
            "altitude": alt_ft,
            "heading": int(heading / 360.0 * 65536) % 65536,
            "groundspeed_kt": speed,
        }

    def air(t: float) -> dict[str, Any]:
        out: dict[str, Any] = {"airspeed_kt": 240.0}
        if not v2:
            out["baro_altitude_ft"] = 5020.0 + 100.0 * math.sin(t / 10)
        return out

    return {
        "NavigationState": nav,
        "AirData": air,
        "AttitudeState": lambda t: {
            "roll_deg": 5.0 * math.sin(t),
            "pitch_deg": 2.0 * math.cos(t / 2),
            "yaw_deg": (90.0 + 2.0 * t) % 360.0,
        },
        "PowerStatus": lambda t: {"bus_voltage_v": 28.0, "power_state": 1},
        "TimeReference": lambda t: {"utc_seconds": 1_000_000.0 + t},
    }


@dataclass
class System:
    sched: Scheduler
    platform: BaselinePlatform
    bridge: Bridge | None
    subsystems: dict[str, UniqueSubsystem]
    network: Network
    adapters: dict[str, Any] | None = None  # P2P only: each subsystem's own baseline adapter

    def run(self, seconds: float) -> None:
        self.sched.run_until(seconds)


def build(
    version: int = 1,
    seed: int = 1,
    mapping_file: Path | None = None,
    **bridge_opts: Any,
) -> System:
    """Assemble the baseline, the Bridge and the four unique systems, and start them."""
    sched = Scheduler(seed)
    network = Network()
    iface = interface(version)
    mapping = load_module(mapping_file or HERE / "generated" / f"mapping_v{version}.py")
    platform = BaselinePlatform(sched, iface, sources(version), name="GHAV")
    bridge = Bridge(sched, iface, mapping, network, **bridge_opts)  # type: ignore[arg-type]
    subs = make_subsystems(sched)
    bridge.attach_baseline(platform)
    for sub in subs.values():
        bridge.attach_unique(sub)
    bridge.start()
    platform.start()
    return System(sched, platform, bridge, subs, network)


def build_p2p(version: int = 1, seed: int = 1, adapters_from: int | None = None) -> System:
    """The same subsystems wired point to point.

    ``adapters_from`` is the baseline version the subsystems' own adapters were written
    against (default: the baseline's version). Leaving it at the old version models a
    baseline change that nobody has propagated into the subsystems yet.
    """
    from bridge_island.p2p.direct import attach_direct

    sched = Scheduler(seed)
    network = Network()
    iface = interface(version)
    adapter_version = adapters_from or version
    mapping = load_module(HERE / "generated" / f"mapping_v{adapter_version}.py")
    platform = BaselinePlatform(sched, iface, sources(version), name="GHAV", enforce_bridge=False)
    network.add(platform)
    subs = make_subsystems(sched)
    adapters = {
        name: attach_direct(platform, sub, mapping, interface(adapter_version), network)
        for name, sub in subs.items()
    }
    platform.start()
    return System(sched, platform, None, subs, network, adapters)
