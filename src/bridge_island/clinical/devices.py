"""The insulin loop's Unique Island: invented glucose sensors, insulin pumps and a phone display.

Each device is a :class:`UniqueSubsystem` with its own protocol, units and rate. The sensors sample
the patient model; the pumps' insulin drives it. Fault hooks (dropouts, late delivery, bad values,
silence, throttling) are switched on by the scenario. Synthetic, illustrative; not medical software.
"""

from __future__ import annotations

import functools
import math
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from bridge_island.clinical.physiology import Patient
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum
from bridge_island.unique.subsystem import Need, Output, UniqueSubsystem

MMOL = 18.0182  # mg/dL per mmol/L, as in the units table
SENSOR_LOW, SENSOR_HIGH = 40.0, 400.0
Window = tuple[float, float]


def inside(t: float, windows: Sequence[Window]) -> Window | None:
    return next((w for w in windows if w[0] <= t < w[1]), None)


@dataclass(frozen=True)
class SensorModel:
    """What distinguishes one sensor model from another (all invented)."""

    units: str  # "mg/dL" | "mmol/L"
    period_s: float
    lag_s: float  # first-order lag time constant
    noise_mg_dl: float  # standard deviation of the measurement noise
    status_names: bool  # True: status as names ("OK", ...); False: numeric codes


class Sensor(UniqueSubsystem):
    """A glucose sensor. Reports a lagged, noisy reading every period; 40-400 mg/dL range."""

    STATUS = ("OK", "LOW", "HIGH", "ERR")

    def __init__(
        self,
        sched: Scheduler,
        name: str,
        protocol: str,
        model: SensorModel,
        patient: Patient,
        outputs: tuple[Output, ...],
        seed: str,
        active: Window = (0.0, math.inf),
    ) -> None:
        super().__init__(sched, name, protocol, (), outputs)
        self.model = model
        self.patient = patient
        self.rng = random.Random(seed)
        self.active = active
        self.dropouts: list[Window] = []
        self.delays: list[tuple[float, float, float]] = []  # (start, end, release every s)
        self.overrides: list[tuple[float, float, float | None, int]] = []  # value mg/dL, status
        self.sent: list[
            tuple[float, float, int]
        ] = []  # (sample time, value meant in mg/dL, status)
        self._held: list[dict[str, Any]] = []
        self._lagged = patient.params.Gb
        self._lag_minute = 0

    def start(self, phase_s: float = 0.0) -> None:
        self.sched.every(self.model.period_s, self._sample, start=phase_s)
        for start, end, every in self.delays:
            t = start + every
            while t < end:
                self.sched.at(t, self._release)
                t += every
            self.sched.at(end, self._release)

    def _lag_to(self, now: float) -> float:
        self.patient.advance_to(now)
        trace = self.patient.trace
        alpha = 1.0 - math.exp(-60.0 / self.model.lag_s)
        while self._lag_minute < len(trace) - 1:
            self._lag_minute += 1
            self._lagged += (trace[self._lag_minute] - self._lagged) * alpha
        return self._lagged

    def _sample(self) -> None:
        now = self.sched.now
        lagged = self._lag_to(now)
        if not self.active[0] <= now < self.active[1] or inside(now, self.dropouts):
            return
        value = lagged + self.rng.gauss(0.0, self.model.noise_mg_dl)
        status = 0
        if value < SENSOR_LOW:
            value, status = SENSOR_LOW, 1
        elif value > SENSOR_HIGH:
            value, status = SENSOR_HIGH, 2
        for start, end, forced, code in self.overrides:
            if start <= now < end:
                value = forced if forced is not None else value
                status = code
        self.sent.append((now, round(value), status))
        if self.model.units == "mmol/L":
            reported: float = round(value / MMOL, 1)
        else:
            reported = float(round(value))
        values: dict[str, Any] = {
            "glucose": reported,
            "status": self.STATUS[status] if self.model.status_names else status,
            "sample_time": now,
        }
        if any(s <= now < e for s, e, _ in self.delays):
            self._held.append(values)  # stuck in a phone or relay buffer
            return
        self.send("reading", values)

    def _release(self) -> None:
        held, self._held = self._held, []
        for values in held:
            self.send("reading", values)


class Pump(UniqueSubsystem):
    """An insulin pump. Executes each command when its command id arrives (the last field).

    ``firmware`` "A": basal in U/h, bolus in U. "B" (v2): basal field renamed ``basal_rate``,
    bolus in integer pulses of 0.05 U, reports delivered pulses and ``reservoir_units``. A pump
    does not check command ids (replay protection is not its job here); when a command's
    valid-until time passes with no newer command, it reverts to its programmed basal.
    """

    PULSE_U = 0.05

    def __init__(
        self,
        sched: Scheduler,
        name: str,
        firmware: str,
        needs: tuple[Need, ...],
        outputs: tuple[Output, ...],
        patient: Patient,
        programmed_basal: float,
        active: Window = (0.0, math.inf),
        minor_hz: float = 0.1,
    ) -> None:
        super().__init__(sched, name, "command_response", needs, outputs, bus_minor_hz=minor_hz)
        self.firmware = firmware
        self.patient = patient
        self.programmed_basal = programmed_basal
        self.active = active
        self.mute: list[Window] = []  # accepts and delivers, does not confirm
        self.dead: list[Window] = []  # delivers nothing, does not confirm
        self.executed: list[tuple[float, Any, float, float | None]] = []  # time, id, bolus U, basal
        self.reservoir = 300.0
        self._parts: dict[float, dict[str, Any]] = {}
        self._latest_command_time = -math.inf
        self.patient.set_basal(0.0, programmed_basal)

    def on_data(self, native_name: str, datum: Datum) -> None:
        super().on_data(native_name, datum)
        parts = self._parts.setdefault(datum.source_time, {})
        parts[native_name] = datum.value
        if native_name == "command_id":
            self._parts.pop(datum.source_time)
            self._execute(parts)

    def _execute(self, v: dict[str, Any]) -> None:
        now = self.sched.now
        if not self.active[0] <= now < self.active[1] or inside(now, self.dead):
            return
        basal = v.get("basal_rate" if self.firmware == "B" else "basal")
        bolus_raw = v.get("bolus", 0)
        bolus = round(float(bolus_raw)) * self.PULSE_U if self.firmware == "B" else float(bolus_raw)
        if basal is not None:
            self.patient.set_basal(now, float(basal))
            self._latest_command_time = now
            until = v.get("valid_until")
            if isinstance(until, (int, float)) and math.isfinite(until) and until > now:
                self.sched.at(until, functools.partial(self._expire, now))
        if bolus > 0:
            self.patient.bolus(now, bolus)
            self.reservoir -= bolus
        self.executed.append(
            (now, v.get("command_id"), bolus, None if basal is None else float(basal))
        )
        if inside(now, self.mute):
            return
        if self.firmware == "B":
            values: dict[str, Any] = {
                "command_id": v["command_id"],
                "delivered": round(bolus / self.PULSE_U),
                "status": 0,
                "reservoir_units": round(self.reservoir, 2),
                "battery": 80,
            }
        else:
            values = {
                "command_id": v["command_id"],
                "delivered": round(bolus, 3),
                "status": 0,
                "reservoir": round(self.reservoir, 2),
                "battery": 80,
            }
        self.send("confirm", values)

    def _expire(self, issued_at: float) -> None:
        now = self.sched.now
        if self._latest_command_time == issued_at and self.active[0] <= now < self.active[1]:
            self.patient.set_basal(now, self.programmed_basal)


class Display(UniqueSubsystem):
    """A phone-like companion display. Shows glucose and alarms, acknowledges alarms it receives.

    During a throttle window the phone's operating system holds the app back: each delivery is
    dropped with probability ``drop`` and otherwise handled only at the end of the window.
    """

    def __init__(
        self,
        sched: Scheduler,
        name: str,
        needs: tuple[Need, ...],
        outputs: tuple[Output, ...],
        seed: str,
    ) -> None:
        super().__init__(sched, name, "pubsub", needs, outputs)
        self.rng = random.Random(seed)
        self.throttle: list[Window] = []
        self.drop = 0.5
        self.dropped = 0
        self.alarms_shown: list[tuple[float, str]] = []  # (time shown, what)

    def _throttled(self, handle: Any) -> bool:
        window = inside(self.sched.now, self.throttle)
        if window is None:
            return False
        if self.rng.random() < self.drop:
            self.dropped += 1
        else:
            self.sched.at(window[1], handle)
        return True

    def on_data(self, native_name: str, datum: Datum) -> None:
        if self._throttled(lambda: self._show_data(native_name, datum)):
            return
        self._show_data(native_name, datum)

    def _show_data(self, native_name: str, datum: Datum) -> None:
        super().on_data(native_name, datum)
        if native_name == "alarm_code" and datum.value not in ("NONE", 0):
            self.alarms_shown.append((self.sched.now, f"host:{datum.value}"))

    def on_annunciation(self, kind: str, detail: str) -> None:
        if self._throttled(lambda: self._show_annunciation(kind, detail)):
            return
        self._show_annunciation(kind, detail)

    def _show_annunciation(self, kind: str, detail: str) -> None:
        super().on_annunciation(kind, detail)
        found = re.match(r"#(\d+) ", detail)
        self.alarms_shown.append((self.sched.now, f"{kind}:{detail}"))
        if found and self._uplink is not None:
            self.send("ack", {"alarm_id": int(found.group(1))})
