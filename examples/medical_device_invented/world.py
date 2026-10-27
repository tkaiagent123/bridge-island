"""Generic Infusion Host Platform (GIHP): an invented medical-device example.

Every platform, message, field, rate and number here is invented. Nothing in this example
makes any clinical claim, and it is not medical software.

A cleared host platform (the Baseline Island) publishes pump state, alarm state and a device
clock, and accepts one display request. Three independently owned add-on modules make up the
Unique Island: DoseAdvisor, BedsideDisplay and AlarmRelay.

Baseline interface v2 applies this change set to v1:
  * rename:    PumpState.volume_infused_ml -> total_volume_ml
  * unit:      PumpState.line_pressure kPa -> mmHg
  * encoding:  AlarmState.alarm_code numeric code -> named enum
  * rate:      AlarmState 5 Hz -> 10 Hz
  * added:     PumpState.battery_level
  * removed:   AlarmState.alarm_priority
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from bridge_island.baseline.platform import BaselineInterface
from bridge_island.codegen import Dictionary, load_module
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import DataElement, MessageSpec, Representation
from bridge_island.system import System, build_bridged, build_direct
from bridge_island.unique.subsystem import Need, Output, UniqueSubsystem

HERE = Path(__file__).parent
CR, PS, LW = "command_response", "pubsub", "label_words"
RENAMES = {"PumpState.volume_infused_ml": "PumpState.total_volume_ml"}
ALARM_NAMES = ("NONE", "OCCLUSION", "AIR_IN_LINE", "DOOR_OPEN", "LOW_BATTERY")


def R(encoding: str, units: str, frame: str, protocol: str) -> Representation:
    return Representation(encoding, units, frame, protocol)


def E(name: str, rep: Representation, rng: tuple[float, float] | None = None) -> DataElement:
    return DataElement(name, rep, rng)


def f64(units: str, frame: str) -> Representation:
    return R("float64", units, frame, CR)


def interface(version: int = 1) -> BaselineInterface:
    if version not in (1, 2):
        raise ValueError(f"GIHP baseline v{version} is not defined")
    v2 = version == 2
    pump = [
        E("flow_rate", f64("mL/h", "infusion"), (0, 1200)),
        E("total_volume_ml" if v2 else "volume_infused_ml", f64("mL", "infusion"), (0, 5000)),
        E("line_pressure", f64("mmHg", "line"), (0, 750))
        if v2
        else E("line_pressure", f64("kPa", "line"), (0, 100)),
        E("pump_mode", R("uint8", "1", "infusion", CR), (0, 2)),
    ]
    if v2:
        pump.append(E("battery_level", R("uint8", "1", "battery", CR), (0, 100)))
    alarm = [
        E("alarm_code", R("enum", "1", "alarm", CR))
        if v2
        else E("alarm_code", R("uint8", "1", "alarm", CR), (0, 4)),
        E("alarm_active", R("bool", "1", "alarm", CR)),
    ]
    if not v2:
        alarm.insert(1, E("alarm_priority", R("uint8", "1", "alarm", CR), (0, 3)))
    return BaselineInterface(
        version=version,
        published=(
            MessageSpec("PumpState", tuple(pump), rate_hz=10, subaddress=1),
            MessageSpec("AlarmState", tuple(alarm), rate_hz=10 if v2 else 5, subaddress=2),
            MessageSpec("DeviceClock", (E("clock_seconds", f64("s", "device")),), 1, 3),
        ),
        accepted=(
            MessageSpec(
                "DisplayRequest",
                (
                    E("view_id", R("uint8", "1", "display", CR), (1, 3)),
                    E("brightness", R("uint8", "1", "display", CR), (0, 10)),
                ),
                rate_hz=5,
                subaddress=4,
            ),
        ),
    )


def _needs() -> dict[str, tuple[Need, ...]]:
    def n(
        name: str, enc: str, units: str, frame: str, proto: str, hz: float, b: float, s: float
    ) -> Need:
        return Need(name, R(enc, units, frame, proto), hz, b, s)

    return {
        "DoseAdvisor": (
            n("flow_l_h", "float64", "L/h", "infusion", PS, 2, 1.0, 5.0),
            n("volume_ml", "float64", "mL", "infusion", PS, 2, 1.0, 5.0),
            n("pressure_psi", "float64", "psi", "line", PS, 2, 1.0, 5.0),
        ),
        "BedsideDisplay": (
            n("flow_ml_h", "float64", "mL/h", "infusion", PS, 5, 0.5, 3.0),
            n("volume_ml", "float64", "mL", "infusion", PS, 5, 0.5, 3.0),
            n("pressure_kpa", "float64", "kPa", "line", PS, 5, 0.5, 3.0),
            n("mode_name", "enum", "1", "infusion", PS, 5, 0.5, 3.0),
            n("alarm_code", "uint8", "1", "alarm", PS, 5, 0.5, 3.0),
            n("clock_s", "float64", "s", "device", PS, 1, 0.5, 3.0),
        ),
        "AlarmRelay": (
            n("alarm_code", "uint8", "1", "alarm", LW, 10, 0.05, 1.0),
            n("alarm_priority", "uint8", "1", "alarm", LW, 10, 0.05, 1.0),
            n("alarm_active", "bool", "1", "alarm", LW, 10, 0.05, 1.0),
        ),
    }


_PROTOCOLS = {"DoseAdvisor": PS, "BedsideDisplay": PS, "AlarmRelay": LW}
_OUTPUTS = {
    "BedsideDisplay": (
        Output(
            "show_view",
            (
                ("view", R("enum", "1", "display", PS)),
                ("brightness", R("uint8", "1", "display", PS)),
            ),
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

    def pump(t: float) -> dict[str, Any]:
        pressure_kpa = 20.0 + 2.0 * math.sin(t / 3)
        out: dict[str, Any] = {
            "flow_rate": 125.0 + 5.0 * math.sin(t / 5),
            "line_pressure": pressure_kpa * 7.50062 if v2 else pressure_kpa,
            "pump_mode": 1,
        }
        out["total_volume_ml" if v2 else "volume_infused_ml"] = 10.0 + 125.0 / 3600.0 * t
        if v2:
            out["battery_level"] = 80
        return out

    def alarm(t: float) -> dict[str, Any]:
        out: dict[str, Any] = {"alarm_code": "NONE" if v2 else 0, "alarm_active": False}
        if not v2:
            out["alarm_priority"] = 0
        return out

    return {
        "PumpState": pump,
        "AlarmState": alarm,
        "DeviceClock": lambda t: {"clock_seconds": 5000.0 + t},
    }


def build(
    version: int = 1, seed: int = 1, mapping_file: Path | None = None, **bridge_opts: Any
) -> System:
    """The baseline, the Bridge and the three add-on modules, started."""
    mapping = load_module(mapping_file or HERE / "generated" / f"mapping_v{version}.py")
    return build_bridged(
        interface(version), sources(version), mapping, make_subsystems, seed, "GIHP", **bridge_opts
    )


def build_p2p(version: int = 1, seed: int = 1, adapters_from: int | None = None) -> System:
    adapter_version = adapters_from or version
    mapping = load_module(HERE / "generated" / f"mapping_v{adapter_version}.py")
    return build_direct(
        interface(version),
        sources(version),
        mapping,
        interface(adapter_version),
        make_subsystems,
        seed,
        "GIHP",
    )


NAME = "medical"
VERSIONS = (1, 2)
TIMING = {
    "slow": ("BedsideDisplay", "clock_s"),
    "fast": ("AlarmRelay", "alarm_code"),
    "stall": (2.2, 1.5),
}
CONTAINMENT = {
    "sender": "BedsideDisplay",
    "message": "show_view",
    "good": {"view": "SUMMARY", "brightness": 5},
    "out_of_range": {"view": "SUMMARY", "brightness": 99},
    "bad_enum": {"view": "NOPE", "brightness": 5},
    "malformed": {"view": "SUMMARY"},
    "unauthorized": "set_rate",
}
__all__ = ["System"]
