"""Generic Host Air Vehicle (GHAV): an invented aviation example.

Every platform, message, field, rate and number here is invented.
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


def R(encoding: str, units: str, frame: str, protocol: str) -> Representation:
    return Representation(encoding, units, frame, protocol)


def E(name: str, rep: Representation, rng: tuple[float, float] | None = None) -> DataElement:
    return DataElement(name, rep, rng)


def interface(version: int = 1) -> BaselineInterface:
    if version != 1:
        raise ValueError(f"GHAV baseline v{version} is not defined")
    f64 = lambda units, frame: R("float64", units, frame, CR)  # noqa: E731
    return BaselineInterface(
        version=1,
        published=(
            MessageSpec(
                "NavigationState",
                (
                    E("latitude", f64("deg", "wgs84"), (-90, 90)),
                    E("longitude", f64("deg", "wgs84"), (-180, 180)),
                    E("altitude_ft", f64("ft", "msl"), (-1500, 60000)),
                    E("heading", R("bam16", "deg", "true_north", CR)),
                    E("groundspeed_kt", f64("kt", "ground"), (0, 1500)),
                ),
                rate_hz=20,
                subaddress=1,
            ),
            MessageSpec(
                "AirData",
                (
                    E("baro_altitude_ft", f64("ft", "msl"), (-1500, 60000)),
                    E("airspeed_kt", f64("kt", "air"), (0, 1500)),
                ),
                rate_hz=10,
                subaddress=2,
            ),
            MessageSpec(
                "AttitudeState",
                (
                    E("roll_deg", f64("deg", "body"), (-180, 180)),
                    E("pitch_deg", f64("deg", "body"), (-90, 90)),
                    E("yaw_deg", f64("deg", "body"), (0, 360)),
                ),
                rate_hz=50,
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
                "TimeReference",
                (E("utc_seconds", f64("s", "utc")),),
                rate_hz=1,
                subaddress=5,
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
        # Speaks the baseline representation exactly: every path is an identity (G2).
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

    def nav(t: float) -> dict[str, Any]:
        return {
            "latitude": 41.0 + 1e-4 * t,
            "longitude": -72.0 + 1.5e-4 * t,
            "altitude_ft": 5000.0 + 100.0 * math.sin(t / 10),
            "heading": int(((90.0 + 2.0 * t) % 360.0) / 360.0 * 65536) % 65536,
            "groundspeed_kt": 250.0 + 5.0 * math.sin(t / 7),
        }

    return {
        "NavigationState": nav,
        "AirData": lambda t: {
            "baro_altitude_ft": 5020.0 + 100.0 * math.sin(t / 10),
            "airspeed_kt": 240.0,
        },
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
    bridge: Bridge
    subsystems: dict[str, UniqueSubsystem]
    network: Network

    def run(self, seconds: float) -> None:
        self.sched.run_until(seconds)


def build(
    version: int = 1, seed: int = 1, mapping_file: Path | None = None, **bridge_opts: Any
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
