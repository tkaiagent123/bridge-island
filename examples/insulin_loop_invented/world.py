"""Generic Glycemic Loop (GGL): an invented closed-loop example (insulin-loop).

Every device, message, field, rate and number here is invented. Synthetic, illustrative. Not
medical software, not a validated patient model or dosing algorithm, not for clinical use.

The Baseline Island is an invented, already-cleared dosing controller host (the Controller Host).
It accepts GlucoseReading, DeliveryConfirmation and AlarmAck, and publishes DoseCommand every
5 minutes and ControllerStatus every minute. The Unique Island is independently owned peripherals:
SensorA (mg/dL, every 5 min, label words), SensorB (mmol/L, every minute, pub/sub, a later model
from another maker), PumpA, PumpB firmware v2 (a renamed field, bolus in 0.05 U pulses, a new
reservoir field) and a phone-like CompanionDisplay. The patient model sits outside both islands.

Baseline interface v2 applies this change set to v1:
  * rename:  ControllerStatus.iob -> insulin_on_board
  * unit:    DoseCommand.basal U/h -> mU/min
  * added:   ControllerStatus.trend
  * removed: ControllerStatus.loop_mode
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bridge_island.baseline.platform import BaselineInterface
from bridge_island.codegen import Dictionary
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import DataElement, MessageSpec, Representation
from bridge_island.system import System
from bridge_island.unique.subsystem import Need, Output, UniqueSubsystem

HERE = Path(__file__).parent
CR, PS, LW = "command_response", "pubsub", "label_words"
RENAMES = {"ControllerStatus.iob": "ControllerStatus.insulin_on_board"}
DEFAULT_DEVICES = ("SensorA", "PumpA", "CompanionDisplay")
ALL_DEVICES = ("SensorA", "SensorB", "PumpA", "PumpB", "CompanionDisplay")
MINOR_HZ = 1 / 30  # 30-second bus frames: the loop runs in minutes, not milliseconds


def R(encoding: str, units: str, frame: str, protocol: str = CR) -> Representation:
    return Representation(encoding, units, frame, protocol)


def E(name: str, rep: Representation, rng: tuple[float, float] | None = None) -> DataElement:
    return DataElement(name, rep, rng)


def interface(version: int = 1) -> BaselineInterface:
    if version not in (1, 2):
        raise ValueError(f"GGL baseline v{version} is not defined")
    v2 = version == 2
    command = (
        E("command_id", R("uint32", "1", "command"), (0, 2**32 - 1)),
        E("basal", R("float64", "mU/min" if v2 else "U/h", "dose"), (0, 170 if v2 else 10)),
        E("bolus", R("float64", "U", "dose"), (0, 25)),
        E("valid_until", R("float64", "s", "time"), (0, 1e7)),
    )
    status = [
        E("glucose", R("float64", "mg/dL", "glucose"), (0, 1000)),
        E("alarm_code", R("uint8", "1", "alarm"), (0, 4)),
    ]
    if v2:
        status += [
            E("insulin_on_board", R("float64", "U", "dose"), (0, 100)),
            E("trend", R("float64", "1", "glucose"), (-50, 50)),
        ]
    else:
        status += [
            E("loop_mode", R("uint8", "1", "mode"), (0, 2)),
            E("iob", R("float64", "U", "dose"), (0, 100)),
        ]
    return BaselineInterface(
        version=version,
        published=(
            MessageSpec("ControllerStatus", tuple(status), rate_hz=1 / 60, subaddress=2),
            MessageSpec("DoseCommand", command, rate_hz=1 / 300, subaddress=1),
        ),
        accepted=(
            MessageSpec(
                "GlucoseReading",
                (
                    E("glucose", R("float64", "mg/dL", "glucose"), (0, 1000)),
                    E("sensor_status", R("uint8", "1", "sensor"), (0, 4)),
                    E("source_time", R("float64", "s", "time"), (0, 1e7)),
                ),
                rate_hz=MINOR_HZ,
                subaddress=3,
            ),
            MessageSpec(
                "DeliveryConfirmation",
                (
                    E("command_id", R("uint32", "1", "command"), (0, 2**32 - 1)),
                    E("delivered", R("float64", "U", "dose"), (0, 50)),
                    E("pump_status", R("uint8", "1", "pump"), (0, 3)),
                    E("reservoir", R("float64", "U", "dose"), (0, 400)),
                    E("battery", R("uint8", "%", "battery"), (0, 100)),
                ),
                rate_hz=MINOR_HZ,
                subaddress=4,
            ),
            MessageSpec(
                "AlarmAck",
                (E("alarm_id", R("uint32", "1", "alarm"), (0, 10**6)),),
                rate_hz=MINOR_HZ,
                subaddress=5,
            ),
        ),
    )


def _need(name: str, rep: Representation, hz: float, budget: float, stale: float) -> Need:
    return Need(name, rep, hz, budget, stale)


NEEDS: dict[str, tuple[Need, ...]] = {
    # command_id is the last field: a pump executes a command when its id arrives
    "PumpA": (
        _need("basal", R("float64", "U/h", "dose"), MINOR_HZ, 30, 900),
        _need("bolus", R("float64", "U", "dose"), MINOR_HZ, 30, 900),
        _need("valid_until", R("float64", "s", "time"), MINOR_HZ, 30, 900),
        _need("command_id", R("uint32", "1", "command"), MINOR_HZ, 30, 900),
    ),
    "PumpB": (
        _need("basal_rate", R("float64", "U/h", "dose"), MINOR_HZ, 30, 900),
        _need("bolus", R("uint16", "pulse", "dose"), MINOR_HZ, 30, 900),
        _need("valid_until", R("float64", "s", "time"), MINOR_HZ, 30, 900),
        _need("command_id", R("uint32", "1", "command"), MINOR_HZ, 30, 900),
    ),
    "CompanionDisplay": (
        _need("glucose", R("float64", "mg/dL", "glucose", PS), 1 / 60, 30, 600),
        _need("loop_mode", R("enum", "1", "mode", PS), 1 / 60, 30, 600),
        _need("alarm_code", R("enum", "1", "alarm", PS), 1 / 60, 30, 600),
        _need("iob", R("float64", "U", "dose", PS), 1 / 60, 30, 600),
    ),
}
OUTPUTS: dict[str, tuple[Output, ...]] = {
    "SensorA": (
        Output(
            "reading",
            (
                ("glucose", R("float64", "mg/dL", "glucose", LW)),
                ("status", R("uint8", "1", "sensor", LW)),
                ("sample_time", R("float64", "s", "time", LW)),
            ),
        ),
    ),
    "SensorB": (
        Output(
            "reading",
            (
                ("glucose", R("float64", "mmol/L", "glucose", PS)),
                ("status", R("enum", "1", "sensor", PS)),
                ("sample_time", R("float64", "s", "time", PS)),
            ),
        ),
    ),
    "PumpA": (
        Output(
            "confirm",
            (
                ("command_id", R("uint32", "1", "command")),
                ("delivered", R("float64", "U", "dose")),
                ("status", R("uint8", "1", "pump")),
                ("reservoir", R("float64", "U", "dose")),
                ("battery", R("uint8", "%", "battery")),
            ),
        ),
    ),
    "PumpB": (
        Output(
            "confirm",
            (
                ("command_id", R("uint32", "1", "command")),
                ("delivered", R("uint16", "pulse", "dose")),
                ("status", R("uint8", "1", "pump")),
                ("reservoir_units", R("float64", "U", "dose")),
                ("battery", R("uint8", "%", "battery")),
            ),
        ),
    ),
    "CompanionDisplay": (Output("ack", (("alarm_id", R("uint32", "1", "alarm", PS)),)),),
}
PROTOCOLS = {
    "SensorA": LW,
    "SensorB": PS,
    "PumpA": CR,
    "PumpB": CR,
    "CompanionDisplay": PS,
}


def dictionary(version: int = 1, devices: Sequence[str] = DEFAULT_DEVICES) -> Dictionary:
    unknown = sorted(set(devices) - set(ALL_DEVICES))
    if unknown:
        raise ValueError(f"unknown devices {unknown}; choose from {list(ALL_DEVICES)}")
    return Dictionary(
        interface(version),
        {d: NEEDS[d] for d in devices if d in NEEDS},
        {d: OUTPUTS[d] for d in devices if d in OUTPUTS},
    )


def make_subsystems(sched: Scheduler) -> dict[str, UniqueSubsystem]:
    """Plain stand-ins for the default devices (no patient attached), for structural checks."""
    return {
        name: UniqueSubsystem(
            sched,
            name,
            PROTOCOLS[name],
            NEEDS.get(name, ()),
            OUTPUTS.get(name, ()),
            bus_minor_hz=MINOR_HZ if PROTOCOLS[name] == CR else None,
        )
        for name in DEFAULT_DEVICES
    }


def sources(version: int = 1) -> dict[str, Any]:
    """The host's outputs, from a controller for the first virtual patient on a nominal day."""
    from bridge_island.clinical import loop

    return loop.host_sources(loop.default_controller(version))


def build(
    version: int = 1, seed: int = 1, mapping_file: Path | None = None, **bridge_opts: Any
) -> System:
    """The closed loop for the first virtual patient on a nominal day: host, Bridge with the
    contract monitor, SensorA, PumpA and the CompanionDisplay, started."""
    from bridge_island.clinical import loop

    return loop.build_default(version, seed, mapping_file, **bridge_opts)


def build_p2p(version: int = 1, seed: int = 1, adapters_from: int | None = None) -> System:
    from bridge_island.clinical import loop

    return loop.build_default_direct(version, seed, adapters_from)


# Who owns what (all invented). The Bridge and its contract belong to "Integrator Org (invented)".
OWNERS = {
    "ControllerHost": "Host Maker Org (invented)",
    "SensorA": "Sensor Maker A (invented)",
    "SensorB": "Sensor Maker B (invented)",
    "PumpA": "Pump Maker Org (invented)",
    "PumpB": "Pump Maker Org (invented)",
    "CompanionDisplay": "App Maker Org (invented)",
}
NAME = "insulin-loop"
VERSIONS = (1, 2)
RULE_TIME_SCALE = 600.0  # the rule checks run 600x longer here: data moves in minutes
TIMING = {
    "slow": ("CompanionDisplay", "glucose"),
    "fast": ("PumpA", "command_id"),
    "stall": (3600.0, 1200.0),
}
CONTAINMENT = {
    "sender": "CompanionDisplay",
    "message": "ack",
    "good": {"alarm_id": 1},
    "out_of_range": {"alarm_id": 10**7},
    "bad_enum": {"alarm_id": "one"},
    "malformed": {},
    "unauthorized": "set_basal",
}
__all__ = ["System"]


def _extra_rules() -> list[Any]:
    from bridge_island.clinical.guidelines import RULES

    return RULES


EXTRA_RULES = _extra_rules()
