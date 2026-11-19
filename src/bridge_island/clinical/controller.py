"""The invented dosing controller that runs on the Controller Host (the Baseline Island).

Deliberately simple and deliberately not a real algorithm. Synthetic, illustrative; not medical
software, not a dosing algorithm, not for clinical use. Every 5 minutes it:

* takes the latest glucose reading it has accepted. A reading counts if its status is OK and it is
  no older than ``glucose_timeout_s`` (30 min, the host's own coarse watchdog). It trusts the
  reading otherwise: checking freshness more finely is the interface's job, under the host's
  published interface;
* below ``suspend_below`` (80 mg/dL) it suspends basal; up to ``target`` it gives the programmed
  basal; above ``target`` it adds a proportional correction, ``0.5 (G - target) / ISF - IOB`` units
  spread over an hour, never more than ``max_basal_factor`` times the programmed basal, and none
  at all while insulin on board (IOB) is at or above ``iob_limit_u``;
* without a reading that counts, it gives the programmed basal (open-loop fallback);
* adds a meal bolus, ``0.87 carbs / carb ratio``, for every meal announced since the last cycle;
* raises its own alarms: urgent low, low, no glucose (watchdog), pump communication (no
  confirmation for 30 min, or a confirmation saying the command was not confirmed).

Insulin on board is counted from the pump's confirmations, decaying with a 120-minute time constant.
The settings were tuned once, on the healthy-data day, so that no virtual patient goes below
54 mg/dL; the comparisons then isolate interface effects (a threat to validity, stated in the docs).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bridge_island.clinical.physiology import Therapy
from bridge_island.core.elements import Message

ALARMS = ("NONE", "LOW", "URGENT_LOW", "NO_GLUCOSE", "PUMP_COMM")
MODES = ("CLOSED_LOOP", "FALLBACK", "SUSPEND")
SENSOR_STATUS = ("OK", "LOW", "HIGH", "ERROR", "FAILSAFE")
PUMP_STATUS = ("OK", "OCCLUSION", "LOW_RESERVOIR", "UNCONFIRMED")
OK, FAILSAFE, UNCONFIRMED = 0, 4, 3
USABLE = (0, 1, 2)  # OK, LOW (the sensor reads 40), HIGH (reads 400)


@dataclass(frozen=True)
class Settings:
    target: float = 120.0
    suspend_below: float = 80.0
    low_alarm: float = 70.0
    urgent_low_alarm: float = 54.0
    glucose_timeout_s: float = 1800.0
    pump_timeout_s: float = 1800.0
    cycle_s: float = 300.0
    valid_for_s: float = 720.0
    max_basal_factor: float = 3.0
    iob_tau_min: float = 120.0
    correction_gain: float = 0.5  # half the estimated deficit per hour
    meal_bolus_fraction: float = 0.87  # a slightly cautious meal bolus
    iob_limit_factor: float = 2.5  # IOB limit = this many hours of programmed basal


@dataclass
class Decision:
    """One controller cycle, kept for the metrics (what it used and what it commanded)."""

    time: float
    command_id: int
    glucose: float | None
    glucose_age_s: float | None
    glucose_status: int | None
    basal_u_h: float
    bolus_u: float
    mode: str


@dataclass
class Controller:
    therapy: Therapy
    meals: list[tuple[float, float]]  # announced meals: (time s, carbs g)
    version: int = 1
    settings: Settings = field(default_factory=Settings)
    decisions: list[Decision] = field(default_factory=list)
    alarm_log: list[tuple[float, str]] = field(default_factory=list)  # (time, new alarm)

    def __post_init__(self) -> None:
        self._accepted: Callable[[], list[tuple[float, Message]]] = list
        self._seen = 0
        self.glucose: dict[str, Any] | None = None  # latest accepted reading's values
        self.last_valid: float | None = None
        self.last_confirm_at = 0.0
        self.unconfirmed_at: float | None = None
        self.issued: dict[int, tuple[float, float, float]] = {}  # id -> (time, basal, bolus)
        self.sent_values: dict[int, dict[str, Any]] = {}  # id -> the DoseCommand values
        self.confirmed: set[int] = set()
        self._iob, self._iob_time = 0.0, 0.0
        self.acks: list[tuple[float, int]] = []
        self._next_id = 1
        self._last_cycle = -math.inf
        self._memo: dict[tuple[str, float], dict[str, Any]] = {}
        self.alarm = "NONE"
        self.mode = "CLOSED_LOOP"
        self._prev_glucose: tuple[float, float] | None = None

    def attach(self, accepted: Callable[[], list[tuple[float, Message]]]) -> None:
        """Read the host's accepted inputs (the platform's ``accepted`` list) from now on."""
        self._accepted = accepted

    # ---- inputs -----------------------------------------------------------------------

    def _read_inputs(self, now: float) -> None:
        accepted = self._accepted()
        for _, msg in accepted[self._seen :]:
            v = msg.values
            if msg.spec_name == "GlucoseReading":
                self.glucose = dict(v)
            elif msg.spec_name == "DeliveryConfirmation":
                self.last_confirm_at = now
                if v["pump_status"] == UNCONFIRMED:
                    self.unconfirmed_at = now
                issued = self.issued.get(v["command_id"])
                cid = v["command_id"]
                if (
                    issued is not None
                    and v["pump_status"] != UNCONFIRMED
                    and cid not in self.confirmed
                ):
                    self.confirmed.add(cid)  # a re-send's second confirmation counts once
                    t0, basal, _ = issued
                    extra = (basal - self.therapy.basal_u_h) * self.settings.cycle_s / 3600.0
                    self._add_insulin(now, t0, float(v["delivered"]) + extra)
            elif msg.spec_name == "AlarmAck":
                self.acks.append((now, int(v["alarm_id"])))
        self._seen = len(accepted)

    def _add_insulin(self, now: float, given_at: float, units: float) -> None:
        self.iob(now)
        self._iob += units * math.exp(-(now - given_at) / (self.settings.iob_tau_min * 60.0))

    def iob(self, now: float) -> float:
        """Insulin on board: confirmed insulin above basal, decaying exponentially."""
        if now > self._iob_time:
            self._iob *= math.exp(-(now - self._iob_time) / (self.settings.iob_tau_min * 60.0))
            self._iob_time = now
        return self._iob

    def _usable(self, now: float) -> tuple[float | None, float | None, int | None]:
        g = self.glucose
        if g is None:
            return None, None, None
        age = now - float(g["source_time"])
        status = int(g["sensor_status"])
        if status not in USABLE or age > self.settings.glucose_timeout_s:
            return None, age, status
        return float(g["glucose"]), age, status

    # ---- outputs (the platform's sources call these) ----------------------------------

    def _memoized(
        self, kind: str, now: float, make: Callable[[float], dict[str, Any]]
    ) -> dict[str, Any]:
        """The same time always gives the same values, so a source can be read twice (G2)."""
        key = (kind, now)
        if key not in self._memo:
            self._memo[key] = make(now)
        return self._memo[key]

    def outputs(self, kind: str) -> list[tuple[float, dict[str, Any]]]:
        """Every ``"command"`` or ``"status"`` the host published, in time order."""
        return sorted((t, v) for (k, t), v in self._memo.items() if k == kind)

    def command(self, now: float) -> dict[str, Any]:
        return self._memoized("command", now, self._command)

    def status(self, now: float) -> dict[str, Any]:
        return self._memoized("status", now, self._status)

    def _command(self, now: float) -> dict[str, Any]:
        self._read_inputs(now)
        s, th = self.settings, self.therapy
        glucose, age, status = self._usable(now)
        iob = self.iob(now)
        if glucose is None:
            basal, mode = th.basal_u_h, "FALLBACK"
        elif glucose < s.suspend_below:
            basal, mode = 0.0, "SUSPEND"
        elif glucose <= s.target:
            basal, mode = th.basal_u_h, "CLOSED_LOOP"
        else:
            wanted = s.correction_gain * (glucose - s.target) / th.isf_mg_dl_per_u - iob
            limit = s.iob_limit_factor * th.basal_u_h
            extra = max(0.0, wanted) if iob < limit else 0.0
            basal = min(th.basal_u_h + extra, s.max_basal_factor * th.basal_u_h)
            mode = "CLOSED_LOOP"
        carbs = sum(c for t, c in self.meals if self._last_cycle < t <= now)
        bolus = round(s.meal_bolus_fraction * carbs / th.carb_ratio_g_per_u, 2)
        self._last_cycle = now
        command_id = self._next_id
        self._next_id += 1
        self.issued[command_id] = (now, basal, bolus)
        self.mode = mode
        self.decisions.append(Decision(now, command_id, glucose, age, status, basal, bolus, mode))
        self._update_alarm(now, glucose)
        if glucose is not None:
            self._prev_glucose = (now, glucose)
        values: dict[str, Any] = {
            "command_id": command_id,
            "bolus": bolus,
            "valid_until": now + s.valid_for_s,
        }
        values["basal"] = basal if self.version == 1 else basal / 0.06  # v2: mU/min
        self.sent_values[command_id] = values
        return values

    def _update_alarm(self, now: float, glucose: float | None) -> None:
        s = self.settings
        _, age, _ = self._usable(now)
        pump_lost = now - self.last_confirm_at > s.pump_timeout_s or (
            self.unconfirmed_at is not None and now - self.unconfirmed_at < s.pump_timeout_s
        )
        if glucose is not None and glucose < s.urgent_low_alarm:
            alarm = "URGENT_LOW"
        elif glucose is not None and glucose < s.low_alarm:
            alarm = "LOW"
        elif (
            glucose is None
            and (age is None or age > s.glucose_timeout_s)
            and now >= s.glucose_timeout_s
        ):
            alarm = "NO_GLUCOSE"
        elif pump_lost and now >= s.pump_timeout_s:
            alarm = "PUMP_COMM"
        else:
            alarm = "NONE"
        if alarm != self.alarm and alarm != "NONE":
            self.alarm_log.append((now, alarm))
        self.alarm = alarm

    def _status(self, now: float) -> dict[str, Any]:
        self._read_inputs(now)
        glucose, _, _ = self._usable(now)
        shown = glucose if glucose is not None else 0.0
        values: dict[str, Any] = {
            "glucose": shown,
            "alarm_code": ALARMS.index(self.alarm),
        }
        if self.version == 1:
            values["loop_mode"] = MODES.index(self.mode)
            values["iob"] = round(self.iob(now), 3)
        else:
            values["insulin_on_board"] = round(self.iob(now), 3)
            prev = self._prev_glucose
            values["trend"] = (
                0.0
                if glucose is None or prev is None or now <= prev[0]
                else round((glucose - prev[1]) / ((now - prev[0]) / 60.0), 3)
            )
        return values
