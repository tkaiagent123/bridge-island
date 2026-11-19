"""Assemble and run the insulin loop: one virtual patient, one day, one architecture.

Three architectures, run on the same patients, meals, seeds and faults:

* ``direct``: point to point. Each device has its own adapter on the controller host's bus, written
  for the device version it was integrated with (``p2p.direct``). The adapters carry the same range
  checks as the Bridge's mapping, as a competent integrator would write them.
* ``bridge-basic``: the Bridge, translation only (no contract monitor).
* ``bridge-contract``: the Bridge with the contract monitor on.

Synthetic, illustrative. Not medical software, not for clinical use.
"""

from __future__ import annotations

import functools
import math
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from bridge_island.baseline.platform import BaselinePlatform
from bridge_island.bridge import Bridge
from bridge_island.bridge.contract import ContractSpec, load_contract
from bridge_island.clinical.controller import Controller
from bridge_island.clinical.devices import Display, Pump, Sensor, SensorModel
from bridge_island.clinical.physiology import Params, Patient, Therapy, cohort, therapy_for
from bridge_island.codegen import load_module
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Message
from bridge_island.core.links import Network
from bridge_island.core.transforms import TransformError
from bridge_island.examples import load_world
from bridge_island.p2p.direct import DirectAdapter
from bridge_island.system import System
from bridge_island.unique.subsystem import UniqueSubsystem

ARCHS = ("direct", "bridge-basic", "bridge-contract")
DAY_S = 86400.0
CYCLE_S = 300.0
SENSOR_MODELS = {  # invented
    "SensorA": SensorModel("mg/dL", 300.0, 420.0, 6.0, status_names=False),
    "SensorB": SensorModel("mmol/L", 60.0, 300.0, 5.0, status_names=True),
}
# The invented day: name, clock hour, carbohydrate (g). Each patient's day varies by seed.
MEALS = (
    ("breakfast", 7.5, 45.0),
    ("lunch", 12.5, 65.0),
    ("snack", 16.0, 15.0),
    ("dinner", 19.0, 70.0),
)
FAULT_KINDS = (
    "sensor_swap",
    "pump_swap",
    "sensor_dropout",
    "sensor_delay",
    "sensor_value",
    "pump_silent",
    "replay_command",
    "display_throttle",
    "bridge_stall",
    "meal_eaten",
)


@functools.lru_cache(maxsize=1)
def world() -> ModuleType:
    return load_world("insulin-loop")


@functools.lru_cache(maxsize=1)
def contract_spec() -> ContractSpec:
    return load_contract(Path(world().HERE) / "contract.yaml")


@functools.lru_cache(maxsize=8)
def patients(seed: int = 1) -> tuple[Params, ...]:
    return tuple(cohort(seed)[0])


# ---- the day and the faults ----------------------------------------------------------------


@dataclass(frozen=True)
class Meal:
    name: str
    time: float  # s
    carbs: float  # g announced (and eaten, unless a fault says otherwise)


def meal_plan(params: Params, seed: int) -> tuple[Meal, ...]:
    rng = random.Random(f"meals-{seed}-{params.id}")
    return tuple(
        Meal(
            name,
            round(hour * 60 + rng.uniform(-20, 20)) * 60.0,
            round(carbs * rng.uniform(0.8, 1.2)),
        )
        for name, hour, carbs in MEALS
    )


@dataclass(frozen=True)
class Fault:
    kind: str
    at: str  # "HH:MM", or "<meal>+<minutes>" such as "lunch+15"
    minutes: float = 0.0
    options: tuple[tuple[str, Any], ...] = ()

    def option(self, key: str, default: Any) -> Any:
        return dict(self.options).get(key, default)

    def resolve(self, meals: tuple[Meal, ...]) -> float:
        """The fault's start time, in seconds, for one patient's day."""
        clock = re.fullmatch(r"(\d{1,2}):(\d{2})", self.at)
        if clock:
            return (int(clock.group(1)) * 60 + int(clock.group(2))) * 60.0
        rel = re.fullmatch(r"([a-z]+)\s*([+-]\s*\d+(?:\.\d+)?)?", self.at)
        meal = next((m for m in meals if rel and m.name == rel.group(1)), None)
        if rel is None or meal is None:
            raise ValueError(f"fault time {self.at!r}: use HH:MM or <meal>+<minutes>")
        offset = float(rel.group(2).replace(" ", "")) if rel.group(2) else 0.0
        return meal.time + offset * 60.0


@dataclass(frozen=True)
class LoopScenario:
    name: str
    description: str = ""
    duration_s: float = DAY_S
    seed: int = 1
    faults: tuple[Fault, ...] = ()

    def devices(self) -> str:
        kinds = {f.kind for f in self.faults}
        return (
            "sensor_swap"
            if "sensor_swap" in kinds
            else "pump_swap"
            if "pump_swap" in kinds
            else "default"
        )


NOMINAL = LoopScenario("nominal", "No faults.")


def fault_windows(
    scenario: LoopScenario, meals: tuple[Meal, ...]
) -> list[tuple[str, float, float]]:
    """(kind, start, end) of every fault (a swap is a point; its window runs to the end)."""
    out = []
    for f in scenario.faults:
        if f.kind == "meal_eaten":
            continue
        start = f.resolve(meals)
        if f.kind in ("sensor_swap", "pump_swap"):
            end = scenario.duration_s
        elif (
            f.kind == "sensor_value"
        ):  # bad values, then error codes, at the slowest sensor's period
            end = start + 2 * int(f.option("readings", 3)) * SENSOR_MODELS["SensorA"].period_s
        else:
            end = start + f.minutes * 60
        out.append((f.kind, start, max(end, start + 60.0)))
    return out


# ---- assembly --------------------------------------------------------------------------------


def host_sources(controller: Controller) -> dict[str, Callable[[float], dict[str, Any]]]:
    return {"DoseCommand": controller.command, "ControllerStatus": controller.status}


def default_controller(version: int = 1, seed: int = 1) -> Controller:
    params = patients(seed)[0]
    meals = meal_plan(params, seed)
    return Controller(therapy_for(params), [(m.time, m.carbs) for m in meals], version)


@dataclass
class Loop:
    """Everything in one run, kept for the metrics and the event record."""

    arch: str
    params: Params
    therapy: Therapy
    scenario: LoopScenario
    meals: tuple[Meal, ...]
    patient: Patient
    controller: Controller
    system: System
    devices: dict[str, UniqueSubsystem]
    adapters: dict[str, DirectAdapter] = field(default_factory=dict)
    adapter_errors: list[tuple[float, str, str]] = field(default_factory=list)
    replays: list[tuple[float, int]] = field(default_factory=list)

    @property
    def bridge(self) -> Bridge | None:
        return self.system.bridge

    @property
    def platform(self) -> BaselinePlatform:
        return self.system.platform

    def sensor(self, name: str) -> Sensor:
        dev = self.devices[name]
        assert isinstance(dev, Sensor)
        return dev

    def pump(self, name: str) -> Pump:
        dev = self.devices[name]
        assert isinstance(dev, Pump)
        return dev

    @property
    def display(self) -> Display:
        dev = self.devices["CompanionDisplay"]
        assert isinstance(dev, Display)
        return dev

    def run(self) -> None:
        self.system.run(self.scenario.duration_s)
        self.patient.advance_to(self.scenario.duration_s)


MAPPINGS = {
    "default": "mapping_v{v}.py",
    "sensor_swap": "mapping_v1_sensor_swap.py",
    "pump_swap": "mapping_v1_pump_swap.py",
}
DEVICE_SETS = {
    "default": ("SensorA", "PumpA", "CompanionDisplay"),
    "sensor_swap": ("SensorA", "SensorB", "PumpA", "CompanionDisplay"),
    "pump_swap": ("SensorA", "PumpA", "PumpB", "CompanionDisplay"),
}


def _devices(
    sched: Scheduler,
    names: tuple[str, ...],
    patient: Patient,
    therapy: Therapy,
    seed: int,
    pid: str,
) -> dict[str, UniqueSubsystem]:
    w = world()
    out: dict[str, UniqueSubsystem] = {}
    for name in names:
        if name.startswith("Sensor"):
            out[name] = Sensor(
                sched,
                name,
                w.PROTOCOLS[name],
                SENSOR_MODELS[name],
                patient,
                w.OUTPUTS[name],
                f"{name}-{seed}-{pid}",
            )
        elif name.startswith("Pump"):
            out[name] = Pump(
                sched,
                name,
                name[-1],
                w.NEEDS[name],
                w.OUTPUTS[name],
                patient,
                therapy.basal_u_h,
                minor_hz=w.MINOR_HZ,
            )
        else:
            out[name] = Display(
                sched, name, w.NEEDS[name], w.OUTPUTS[name], f"display-{seed}-{pid}"
            )
    return out


class PortAdapter(DirectAdapter):
    """A direct adapter for one host port. Another device can be plugged into the port later;
    the adapter keeps doing what it was written to do for the port's original device."""

    def __init__(
        self, platform: BaselinePlatform, sub: UniqueSubsystem, mapping: ModuleType, iface: Any
    ) -> None:
        super().__init__(platform, sub, mapping, iface)
        self.port = sub.name

    def _on_message(self, msg: Message) -> None:
        now = self.sub.sched.now
        outs, errors = self.mapping.translate(msg.spec_name, msg.values)
        self.errors += [(now, ref, why) for ref, why in errors if ref in self._refs]
        for _, s, native, value, _ in outs:
            need = self.sub.need(native)
            if s != self.port or need is None:
                continue  # a field the plugged-in device does not have is simply not delivered
            last = self._last_sent.get(native)
            if last is not None and now - last < 1.0 / need.rate_hz - 1e-9:
                continue
            self._last_sent[native] = now
            self.sub.on_data(native, Datum(native, value, msg.source_time))


def _plug(loop: Loop, port: str, device: UniqueSubsystem, at: float) -> None:
    """Direct only: ``device`` is plugged into the host port whose adapter was written for ``port``.

    The adapter is not updated (the integration lag the scenario models): it translates the new
    device's messages as if they came from the old one, and feeds it the old device's fields.
    """
    adapter = loop.adapters[port]
    sched = loop.system.sched

    def send(msg: Message) -> None:
        try:
            name, values = adapter.mapping.translate_up(port, msg.spec_name, msg.values)
        except TransformError as exc:
            loop.adapter_errors.append((sched.now, f"{port} adapter", f"{msg.spec_name}: {exc}"))
            return
        adapter.uplink_queue.append(Message(name, values, msg.source_time))

    device._uplink = send
    if device.needs:
        sched.at(at, lambda: setattr(adapter, "sub", device))


def build_loop(
    arch: str,
    params: Params,
    scenario: LoopScenario = NOMINAL,
    version: int = 1,
    mapping_file: Path | None = None,
    **bridge_opts: Any,
) -> Loop:
    if arch not in ARCHS:
        raise ValueError(f"unknown architecture {arch!r}; choose from {list(ARCHS)}")
    w = world()
    seed = scenario.seed
    sched = Scheduler(seed)
    network = Network()
    therapy = therapy_for(params)
    meals = meal_plan(params, seed)
    eaten = {
        f.option("meal", "lunch"): f.option("fraction", 1.0)
        for f in scenario.faults
        if f.kind == "meal_eaten"
    }
    patient = Patient(params)
    controller = Controller(therapy, [(m.time, m.carbs) for m in meals], version)
    iface = w.interface(version)
    kind = scenario.devices()
    if kind != "default" and version != 1:
        raise ValueError("device-swap scenarios are defined on baseline v1")
    mapping = load_module(
        mapping_file or Path(w.HERE) / "generated" / MAPPINGS[kind].format(v=version)
    )
    devices = _devices(sched, DEVICE_SETS[kind], patient, therapy, seed, params.id)
    if arch == "direct":
        platform = BaselinePlatform(
            sched,
            iface,
            host_sources(controller),
            "ControllerHost",
            enforce_bridge=False,
            minor_hz=w.MINOR_HZ,
        )
        network.add(platform)
        old = DEVICE_SETS["default"]
        adapters: dict[str, DirectAdapter] = {}
        for n in old:
            adapters[n] = PortAdapter(platform, devices[n], mapping, iface)
            network.add(devices[n])
            network.connect(n, platform.name, "command_response")
        system = System(sched, platform, None, {n: devices[n] for n in old}, network, adapters)
    else:
        contract = (
            contract_spec().with_failsafe_basal(therapy.basal_u_h)
            if arch == "bridge-contract"
            else None
        )
        platform = BaselinePlatform(
            sched, iface, host_sources(controller), "ControllerHost", minor_hz=w.MINOR_HZ
        )
        bridge = Bridge(
            sched, iface, mapping, network, health_period_s=30.0, contract=contract, **bridge_opts
        )
        bridge.attach_baseline(platform)
        for dev in devices.values():
            bridge.attach_unique(dev)
        bridge.start()
        system = System(sched, platform, bridge, devices, network)
        adapters = {}
    controller.attach(lambda: platform.accepted)
    loop = Loop(
        arch, params, therapy, scenario, meals, patient, controller, system, devices, adapters
    )
    for meal in meals:
        sched.at(
            meal.time,
            functools.partial(patient.eat, meal.time, meal.carbs * eaten.get(meal.name, 1.0)),
        )
    _apply_faults(loop)
    platform.start()
    for i, dev in enumerate(devices.values()):
        if isinstance(dev, Sensor):
            dev.start(phase_s=17.0 + 7.0 * i)  # sensors are not aligned with the host's frames
    return loop


def _swap_sensor(loop: Loop, f: Fault, at: float, end: float) -> None:
    loop.sensor("SensorA").active = (0.0, at)
    loop.sensor("SensorB").active = (at, math.inf)
    if loop.arch == "direct":
        _plug(loop, "SensorA", loop.devices["SensorB"], at)


def _swap_pump(loop: Loop, f: Fault, at: float, end: float) -> None:
    loop.pump("PumpA").active = (0.0, at)
    loop.pump("PumpB").active = (at, math.inf)
    loop.system.sched.at(at, lambda: loop.patient.set_basal(at, loop.therapy.basal_u_h))
    if loop.arch == "direct":
        _plug(loop, "PumpA", loop.devices["PumpB"], at)


def _sensors(loop: Loop) -> list[Sensor]:
    return [loop.sensor(n) for n in loop.devices if n.startswith("Sensor")]


def _pumps(loop: Loop) -> list[Pump]:
    return [loop.pump(n) for n in loop.devices if n.startswith("Pump")]


def _dropout(loop: Loop, f: Fault, at: float, end: float) -> None:
    for sensor in _sensors(loop):
        sensor.dropouts.append((at, end))


def _delay(loop: Loop, f: Fault, at: float, end: float) -> None:
    every = float(f.option("release_every_min", 30)) * 60.0
    for sensor in _sensors(loop):
        sensor.delays.append((at, end, every))


def _bad_value(loop: Loop, f: Fault, at: float, end: float) -> None:
    n_bad = int(f.option("readings", 3))
    for sensor in _sensors(loop):
        mid = at + n_bad * sensor.model.period_s
        sensor.overrides += [
            (at, mid, float(f.option("value", 600)), 0),  # a bad value marked OK
            (mid, mid + n_bad * sensor.model.period_s, None, 3),  # then error codes
        ]


def _silent(loop: Loop, f: Fault, at: float, end: float) -> None:
    half = at + f.minutes * 30.0
    for pump in _pumps(loop):
        pump.mute.append((at, half))
        pump.dead.append((half, end))


def _replay_fault(loop: Loop, f: Fault, at: float, end: float) -> None:
    delay = float(f.option("delay_min", 4)) * 60.0
    loop.system.sched.at(at, lambda: _replay(loop, at, delay))


def _throttle(loop: Loop, f: Fault, at: float, end: float) -> None:
    loop.display.throttle.append((at, end))
    loop.display.drop = float(f.option("drop", 0.5))


def _stall(loop: Loop, f: Fault, at: float, end: float) -> None:
    if loop.bridge is not None:  # direct has no Bridge to stall
        loop.bridge.stall(at, end - at)


_FAULTS: dict[str, Callable[[Loop, Fault, float, float], None]] = {
    "sensor_swap": _swap_sensor,
    "pump_swap": _swap_pump,
    "sensor_dropout": _dropout,
    "sensor_delay": _delay,
    "sensor_value": _bad_value,
    "pump_silent": _silent,
    "replay_command": _replay_fault,
    "display_throttle": _throttle,
    "bridge_stall": _stall,
}


def _apply_faults(loop: Loop) -> None:
    for f in loop.scenario.faults:
        if f.kind in _FAULTS:  # meal_eaten is applied where the meals are scheduled
            at = f.resolve(loop.meals)
            _FAULTS[f.kind](loop, f, at, at + f.minutes * 60.0)


def _replay(loop: Loop, after: float, delay: float) -> None:
    """``delay`` after the first bolus command issued at or after ``after``, put that command
    back on the host's bus toward the pump (same id, still inside its valid-until time)."""
    c, sched = loop.controller, loop.system.sched
    cid = next((i for i, (t, _, bolus) in c.issued.items() if t >= after and bolus > 0), None)
    if cid is None:
        if sched.now < loop.scenario.duration_s - CYCLE_S:
            sched.after(60.0, lambda: _replay(loop, after, delay))
        return
    t0 = c.issued[cid][0]
    if sched.now < t0 + delay:
        sched.at(t0 + delay, lambda: _replay(loop, after, delay))
        return
    msg = Message("DoseCommand", dict(c.sent_values[cid]), t0)
    sa = world().interface(c.version).published_message("DoseCommand")
    assert sa is not None
    loop.replays.append((loop.system.sched.now, cid))
    if loop.bridge is not None:
        loop.bridge.oiu.rt.on_receive[sa.subaddress](msg)
    else:
        loop.adapters["PumpA"].rt.on_receive[sa.subaddress](msg)


# ---- the default world, for the structural checks (G1-G8) -----------------------------------


def build_default(
    version: int = 1, seed: int = 1, mapping_file: Path | None = None, **bridge_opts: Any
) -> System:
    arch = "bridge-basic" if bridge_opts.pop("contract", True) is False else "bridge-contract"
    loop = build_loop(
        arch,
        patients(1)[0],
        LoopScenario("nominal", seed=seed),
        version,
        mapping_file,
        **bridge_opts,
    )
    return loop.system


def build_default_direct(
    version: int = 1, seed: int = 1, adapters_from: int | None = None
) -> System:
    if adapters_from not in (None, version):
        raise ValueError("insulin-loop direct adapters are built for the running baseline version")
    loop = build_loop("direct", patients(1)[0], LoopScenario("nominal", seed=seed), version)
    return loop.system
