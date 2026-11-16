"""Contract monitor: a declarative interface contract, checked at run time at the Bridge.

The contract (a YAML file, see ``examples/insulin_loop_invented/contract.yaml``) has eight parts:

1. ``owner`` and ``regulatory_home``: who answers for the contract (text, shown on the card).
2. ``data_items``: name, units, range and encoding of every item that crosses, and its role.
3. ``timing``: the maximum data age per message, measured from the source time (G3), the
   end-to-end latency budget, and how often the monitor checks.
4. ``failure_behavior``: the fail-safe output when data is stale, missing or out of range, the
   command-confirmation rule (re-send at most K times, then alarm and tell the controller), and
   the alarm-delivery rule toward the display.
5. ``security``: which messages may cross in which direction, from which senders, and replay
   protection by command id and valid-until time.
6. ``claims``: named claims the Bridge preserves, each linked to the test that checks it.
7. ``change_envelope``: the peripheral changes the Bridge absorbs without touching the baseline.
8. ``residual``: what the Bridge cannot assure.

The monitor is one more partition of the Bridge (G6). It holds no reference to the other parts:
it acts only through the three callbacks the Bridge hands it (submit toward the baseline, re-send
toward a unique system, raise an alarm on a unique system).

Synthetic, illustrative. This is not medical software.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message
from bridge_island.core.events import EventLog
from bridge_island.yamlutil import YamlError, load_yaml

SECTIONS = (
    "owner",
    "regulatory_home",
    "data_items",
    "timing",
    "failure_behavior",
    "security",
    "claims",
    "change_envelope",
    "residual",
)
ROLES = (
    "glucose_value",
    "glucose_status",
    "glucose_time",
    "command_id",
    "command_valid_until",
    "command_basal",
    "command_bolus",
    "confirm_id",
    "confirm_delivered",
    "confirm_status",
    "ack_id",
    "host_alarm",
)


class ContractError(Exception):
    """The contract file is invalid. The message says what to fix."""


@dataclass(frozen=True)
class DataItem:
    name: str  # "Message.field"
    units: str
    lo: float
    hi: float
    encoding: str
    role: str | None = None

    @property
    def message(self) -> str:
        return self.name.split(".")[0]

    @property
    def field(self) -> str:
        return self.name.split(".", 1)[1]

    def violation(self, value: Any) -> str | None:
        """Why ``value`` breaks this item, or ``None``."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{self.name}={value!r} is not a number"
        if self.encoding.startswith(("uint", "int")) and not float(value).is_integer():
            return f"{self.name}={value!r} is not an integer ({self.encoding})"
        if not (math.isfinite(value) and self.lo <= value <= self.hi):
            return f"{self.name}={value!r} outside [{self.lo:g}, {self.hi:g}] {self.units}"
        return None


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class ContractSpec:
    owner: str
    regulatory_home: str
    items: tuple[DataItem, ...]
    max_age_s: Mapping[str, float]
    latency_budget_s: float
    check_period_s: float
    flag_margin_s: float
    failsafe_basal_u_h: float | None  # None: "programmed", filled in when installed
    failsafe_status: int
    ok_statuses: frozenset[int]
    unconfirmed_status: int
    confirm_timeout_s: float
    confirm_max_resends: int
    alarm_target: str
    alarm_ack_timeout_s: float
    alarm_max_resends: int
    down: frozenset[str]
    up: frozenset[str]
    sources: Mapping[str, frozenset[str]]
    claims: tuple[Claim, ...]
    change_envelope: Mapping[str, Any]
    residual: tuple[str, ...]
    raw: Mapping[str, Any] = field(compare=False, repr=False, default_factory=dict)

    def role(self, role: str) -> DataItem | None:
        return next((i for i in self.items if i.role == role), None)

    def items_of(self, message: str) -> tuple[DataItem, ...]:
        return tuple(i for i in self.items if i.message == message)

    def with_failsafe_basal(self, basal_u_h: float) -> ContractSpec:
        """The contract as installed for one patient: the programmed basal is the fail-safe."""
        from dataclasses import replace

        return replace(self, failsafe_basal_u_h=float(basal_u_h))


# --- loading ------------------------------------------------------------------------------


def _need(d: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise ContractError(f"{where}: missing '{key}'")
    return d[key]


def _num(value: Any, where: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{where} must be a finite number, got {value!r}")
    if value < minimum:
        raise ContractError(f"{where} must be >= {minimum:g}, got {value!r}")
    return float(value)


def _int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{where} must be a whole number >= 0, got {value!r}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{where} must be non-empty text, got {value!r}")
    return value


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be a mapping")
    return value


def _basal(value: Any) -> float | None:
    if value == "programmed":
        return None
    return _num(value, "failure_behavior.failsafe_basal_u_h")


def _statuses(value: Any) -> frozenset[int]:
    if not isinstance(value, list) or not value:
        raise ContractError("failure_behavior.ok_statuses must be a non-empty list")
    return frozenset(_int(v, "failure_behavior.ok_statuses") for v in value)


def _items(raw: Any) -> tuple[DataItem, ...]:
    if not isinstance(raw, list) or not raw:
        raise ContractError("data_items must be a non-empty list")
    out = []
    for i, entry in enumerate(raw):
        where = f"data_items[{i}]"
        e = _mapping(entry, where)
        name = _text(_need(e, "name", where), f"{where}.name")
        if name.count(".") != 1:
            raise ContractError(f"{where}.name must be Message.field, got {name!r}")
        rng = _need(e, "range", where)
        if not isinstance(rng, list) or len(rng) != 2:
            raise ContractError(f"{where}.range must be [low, high]")
        lo, hi = (
            _num(rng[0], f"{where}.range", -math.inf),
            _num(rng[1], f"{where}.range", -math.inf),
        )
        if lo > hi:
            raise ContractError(f"{where}.range low {lo:g} is above high {hi:g}")
        role = e.get("role")
        if role is not None and role not in ROLES:
            raise ContractError(f"{where}.role {role!r} is not one of {list(ROLES)}")
        out.append(
            DataItem(
                name,
                _text(_need(e, "units", where), f"{where}.units"),
                lo,
                hi,
                _text(_need(e, "encoding", where), f"{where}.encoding"),
                role,
            )
        )
    roles = [i.role for i in out if i.role]
    if len(roles) != len(set(roles)):
        raise ContractError("data_items: each role may be given to one item only")
    return tuple(out)


def parse_contract(data: Any, source: str = "contract") -> ContractSpec:
    """Check every section and build the spec. Raises :class:`ContractError` only."""
    d = _mapping(data, source)
    missing = [s for s in SECTIONS if s not in d]
    unknown = sorted(set(d) - set(SECTIONS))
    if missing or unknown:
        raise ContractError(f"{source}: missing sections {missing}, unknown sections {unknown}")
    items = _items(d["data_items"])
    timing = _mapping(d["timing"], "timing")
    ages = _mapping(_need(timing, "max_age_s", "timing"), "timing.max_age_s")
    fb = _mapping(d["failure_behavior"], "failure_behavior")
    conf = _mapping(_need(fb, "confirmation", "failure_behavior"), "failure_behavior.confirmation")
    alarm = _mapping(
        _need(fb, "alarm_delivery", "failure_behavior"), "failure_behavior.alarm_delivery"
    )
    sec = _mapping(d["security"], "security")
    sources = _mapping(_need(sec, "sources", "security"), "security.sources")
    claims_raw = d["claims"]
    if not isinstance(claims_raw, list) or not claims_raw:
        raise ContractError("claims must be a non-empty list")
    claims = []
    for i, c in enumerate(claims_raw):
        c = _mapping(c, f"claims[{i}]")
        tests = _need(c, "tests", f"claims[{i}]")
        if not isinstance(tests, list) or not tests or not all(isinstance(t, str) for t in tests):
            raise ContractError(f"claims[{i}].tests must be a non-empty list of test names")
        claims.append(
            Claim(
                _text(_need(c, "id", f"claims[{i}]"), f"claims[{i}].id"),
                _text(_need(c, "text", f"claims[{i}]"), f"claims[{i}].text"),
                tuple(tests),
            )
        )
    residual = d["residual"]
    if not isinstance(residual, list) or not residual:
        raise ContractError("residual must be a non-empty list")
    up = frozenset(_need(sec, "up", "security"))
    for msg, senders in sources.items():
        if msg not in up or not isinstance(senders, list) or not senders:
            raise ContractError(f"security.sources.{msg}: must be an 'up' message with senders")
    spec = ContractSpec(
        owner=_text(d["owner"], "owner"),
        regulatory_home=_text(d["regulatory_home"], "regulatory_home"),
        items=items,
        max_age_s={str(k): _num(v, f"timing.max_age_s.{k}", 1e-9) for k, v in ages.items()},
        latency_budget_s=_num(
            _need(timing, "latency_budget_s", "timing"), "timing.latency_budget_s"
        ),
        check_period_s=_num(
            _need(timing, "check_period_s", "timing"), "timing.check_period_s", 1e-6
        ),
        flag_margin_s=_num(timing.get("flag_margin_s", 0.0), "timing.flag_margin_s"),
        failsafe_basal_u_h=_basal(_need(fb, "failsafe_basal_u_h", "failure_behavior")),
        failsafe_status=_int(_need(fb, "failsafe_status", "failure_behavior"), "failsafe_status"),
        ok_statuses=_statuses(_need(fb, "ok_statuses", "failure_behavior")),
        unconfirmed_status=_int(
            _need(fb, "unconfirmed_status", "failure_behavior"), "unconfirmed_status"
        ),
        confirm_timeout_s=_num(
            _need(conf, "timeout_s", "confirmation"), "confirmation.timeout_s", 1e-6
        ),
        confirm_max_resends=_int(
            _need(conf, "max_resends", "confirmation"), "confirmation.max_resends"
        ),
        alarm_target=_text(_need(alarm, "target", "alarm_delivery"), "alarm_delivery.target"),
        alarm_ack_timeout_s=_num(
            _need(alarm, "ack_timeout_s", "alarm_delivery"), "alarm_delivery.ack_timeout_s", 1e-6
        ),
        alarm_max_resends=_int(
            _need(alarm, "max_resends", "alarm_delivery"), "alarm_delivery.max_resends"
        ),
        down=frozenset(_need(sec, "down", "security")),
        up=up,
        sources={str(k): frozenset(v) for k, v in sources.items()},
        claims=tuple(claims),
        change_envelope=_mapping(d["change_envelope"], "change_envelope"),
        residual=tuple(_text(r, "residual") for r in residual),
        raw=d,
    )
    for role in ("glucose_value", "glucose_status", "glucose_time"):
        if spec.role(role) is None:
            raise ContractError(f"data_items: no item has the role {role!r}")
    return spec


def load_contract(path: Path) -> ContractSpec:
    try:
        data = load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YamlError) as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc
    return parse_contract(data, path.name)


# --- the monitor --------------------------------------------------------------------------


@dataclass
class _Pending:
    msg: Message
    sent_at: float
    resends: int = 0


@dataclass
class Alarm:
    id: int
    kind: str
    detail: str
    raised_at: float
    resends: int = 0
    acked_at: float | None = None
    undelivered: bool = False


class ContractMonitor:
    """Checks every crossing against the contract and applies its failure behaviour."""

    def __init__(
        self,
        spec: ContractSpec,
        sched: Scheduler,
        log: EventLog,
        submit_up: Callable[[Message], None],
        resend_down: Callable[[Message], None],
        alarm: Callable[[str, str, str], None],
    ) -> None:
        self.spec = spec
        self.sched = sched
        self.log = log
        self._submit_up = submit_up
        self._resend_down = resend_down
        self._alarm = alarm
        if spec.failsafe_basal_u_h is None:
            raise ContractError("install the contract for a patient first: with_failsafe_basal()")
        g = spec.role("glucose_value")
        assert g is not None  # parse_contract guarantees the glucose roles
        self.glucose_msg = g.message
        self.failsafe = False
        self.failsafe_since: float | None = None
        self.last_glucose_time: float | None = None  # newest valid reading to cross the Bridge
        self.held_time: float | None = None  # newest valid reading handed to the controller
        self.started_at = sched.now
        self.seen_ids: set[Any] = set()
        self.pending: dict[Any, _Pending] = {}
        self.alarms: dict[int, Alarm] = {}
        self._last_host_alarm: Any = 0
        self._last_confirm: dict[str, Any] = {}
        self.violations = 0
        self.blocked = 0

    # ---- helpers ----------------------------------------------------------------------

    def _log(self, kind: str, detail: str) -> None:
        self.log.add(self.sched.now, "contract", f"contract.{kind}", detail)

    def raise_alarm(self, kind: str, detail: str) -> Alarm:
        alarm = Alarm(len(self.alarms) + 1, kind, detail, self.sched.now)
        self.alarms[alarm.id] = alarm
        self._log("alarm", f"#{alarm.id} {kind}: {detail}")
        self._alarm(self.spec.alarm_target, "contract", f"#{alarm.id} {kind}: {detail}")
        return alarm

    def _field(self, role: str) -> str:
        item = self.spec.role(role)
        assert item is not None
        return item.field

    def _failsafe_reading(self) -> Message:
        values = {
            self._field("glucose_value"): 0.0,
            self._field("glucose_status"): self.spec.failsafe_status,
            self._field("glucose_time"): self.sched.now,
        }
        return Message(self.glucose_msg, values, self.sched.now)

    def _enter_failsafe(self, reason: str) -> Message:
        """Fail-safe first (G7): tell the controller, flag it, annunciate it (G8)."""
        self.violations += 1
        if not self.failsafe:
            self.failsafe, self.failsafe_since = True, self.sched.now
            self._log("failsafe", reason)
            self.raise_alarm("failsafe", reason)
        else:
            self._log("violation", reason)
        return self._failsafe_reading()

    # ---- crossings ---------------------------------------------------------------------

    def check_up(self, sender: str, msg: Message) -> Message | None:
        """A unique-to-baseline message after translation. Returns what to pass on, or None."""
        name = msg.spec_name
        if name not in self.spec.up or sender not in self.spec.sources.get(name, frozenset()):
            self.blocked += 1
            self._log("block", f"up: {sender} may not send {name}")
            return None
        if name == self.glucose_msg:
            return self._check_glucose(msg)
        for item in self.spec.items_of(name):
            why = item.violation(msg.values.get(item.field))
            if why is not None:
                self.violations += 1
                self._log("violation", why)
                self.raise_alarm("out_of_range", why)
                return None
        conf_id = self.spec.role("confirm_id")
        if conf_id is not None and name == conf_id.message:
            cid = msg.values[conf_id.field]
            self._last_confirm = dict(msg.values)
            if self.pending.pop(cid, None) is not None:
                self._log("confirmed", f"command {cid}")
        ack = self.spec.role("ack_id")
        if ack is not None and name == ack.message:
            alarm = self.alarms.get(msg.values[ack.field])
            if alarm is not None and alarm.acked_at is None:
                alarm.acked_at = self.sched.now
                self._log(
                    "ack",
                    f"#{alarm.id} acknowledged after {self.sched.now - alarm.raised_at:.0f} s",
                )
        return msg

    def _check_glucose(self, msg: Message) -> Message:
        status = msg.values.get(self._field("glucose_status"))
        source_time = msg.values.get(self._field("glucose_time"))
        reasons = [
            why
            for i in self.spec.items_of(msg.spec_name)
            if (why := i.violation(msg.values.get(i.field)))
        ]
        if status not in self.spec.ok_statuses:
            reasons.append(f"sensor status {status!r}")
        max_age = self.spec.max_age_s.get(msg.spec_name)
        if isinstance(source_time, (int, float)) and max_age is not None:
            age = self.sched.now - source_time
            if age > max_age:
                reasons.append(f"reading {age:.0f} s old, limit {max_age:g} s")
        if reasons:
            return self._enter_failsafe("; ".join(reasons))
        assert isinstance(source_time, (int, float))
        self.last_glucose_time = max(self.last_glucose_time or source_time, source_time)
        return msg

    def _flag_age(self) -> float:
        """Readings are flagged this old: the maximum age less the margin for the controller's
        own delay (it may read a reading up to one check period and one host frame later)."""
        return self.spec.max_age_s.get(self.glucose_msg, math.inf) - self.spec.flag_margin_s

    def handover(self, msg: Message) -> Message:
        """The last look as the controller takes a reading: it may have aged in the queue. A
        fresh reading that reaches the controller ends the fail-safe."""
        if msg.spec_name != self.glucose_msg:
            return msg
        status = msg.values.get(self._field("glucose_status"))
        source_time = msg.values.get(self._field("glucose_time"))
        if status == self.spec.failsafe_status or not isinstance(source_time, (int, float)):
            return msg
        age = self.sched.now - source_time
        if age > self._flag_age():
            return self._enter_failsafe(
                f"reading {age:.0f} s old at handover, flagged at {self._flag_age():g} s"
            )
        self.held_time = max(self.held_time or source_time, source_time)
        if self.failsafe:
            self.failsafe, self.failsafe_since = False, None
            self._log("recovered", "valid glucose reached the controller again")
        return msg

    def rejected_up(self, sender: str, message: str, reason: str) -> None:
        """The translator refused a message (for example a value outside its range)."""
        if message and sender in self.spec.sources.get(self.glucose_msg, frozenset()):
            self._submit_up(self._enter_failsafe(f"{sender}.{message} rejected: {reason}"))

    def check_down(self, msg: Message) -> Message | None:
        """A baseline-to-unique message. Returns what to pass on (possibly fail-safe), or None."""
        name = msg.spec_name
        if name not in self.spec.down:
            self.blocked += 1
            self._log("block", f"down: {name} may not cross")
            return None
        host_alarm = self.spec.role("host_alarm")
        if host_alarm is not None and name == host_alarm.message:
            code = msg.values.get(host_alarm.field, 0)
            if code and code != self._last_host_alarm:
                self.raise_alarm("host_alarm", f"controller alarm code {code}")
            self._last_host_alarm = code
        cid = self.spec.role("command_id")
        if cid is None or name != cid.message:
            return msg
        return self._check_command(msg, cid.field)

    def _check_command(self, msg: Message, id_field: str) -> Message | None:
        command_id = msg.values.get(id_field)
        until = self.spec.role("command_valid_until")
        expired = until is not None and msg.values.get(until.field, math.inf) < self.sched.now
        if command_id in self.seen_ids or expired:
            self.blocked += 1
            used = "already used" if command_id in self.seen_ids else "past its valid-until time"
            self._log("replay_blocked", f"command {command_id} {used}")
            self.raise_alarm("replay_blocked", f"command {command_id} {used}")
            return None
        for item in self.spec.items_of(msg.spec_name):
            why = item.violation(msg.values.get(item.field))
            if why is not None:
                self.blocked += 1
                self._log("violation", why)
                self.raise_alarm("out_of_range", why)
                return None
        self.seen_ids.add(command_id)
        basal = self.spec.role("command_basal")
        if self.failsafe and basal is not None:
            values = dict(msg.values)
            values[basal.field] = self.spec.failsafe_basal_u_h
            msg = Message(msg.spec_name, values, msg.source_time)
            self._log(
                "failsafe_basal",
                f"command {command_id}: basal {self.spec.failsafe_basal_u_h:g} U/h",
            )
        if self.spec.role("confirm_id") is not None:
            self.pending[command_id] = _Pending(msg, self.sched.now)
        return msg

    # ---- periodic check ----------------------------------------------------------------

    def scan(self) -> None:
        now = self.sched.now
        max_age = self.spec.max_age_s.get(self.glucose_msg)
        basis = self.held_time if self.held_time is not None else self.started_at
        if max_age is not None and not self.failsafe and now - basis > self._flag_age():
            what = (
                "no glucose" if self.held_time is None else "glucose the controller holds is stale"
            )
            self._submit_up(
                self._enter_failsafe(
                    f"{what}: {now - basis:.0f} s, flagged at {self._flag_age():g} s "
                    f"(limit {max_age:g} s)"
                )
            )
        for command_id, p in list(self.pending.items()):
            if now - p.sent_at < self.spec.confirm_timeout_s * (p.resends + 1):
                continue
            if p.resends < self.spec.confirm_max_resends:
                p.resends += 1
                self._log("resend", f"command {command_id} (re-send {p.resends}, no bolus)")
                self._resend_down(self._without_bolus(p.msg))
                continue
            del self.pending[command_id]
            self._log("unconfirmed", f"command {command_id} after {p.resends} re-sends")
            self.raise_alarm("unconfirmed", f"command {command_id} not confirmed")
            self._tell_unconfirmed(command_id)
        for alarm in self.alarms.values():
            if alarm.acked_at is not None or alarm.undelivered:
                continue
            if now - alarm.raised_at < self.spec.alarm_ack_timeout_s * (alarm.resends + 1):
                continue
            if alarm.resends < self.spec.alarm_max_resends:
                alarm.resends += 1
                self._alarm(
                    self.spec.alarm_target, "contract", f"#{alarm.id} {alarm.kind}: {alarm.detail}"
                )
                continue
            alarm.undelivered = True
            self._log(
                "alarm_undelivered", f"#{alarm.id} {alarm.kind}, age {now - alarm.raised_at:.0f} s"
            )

    def _without_bolus(self, msg: Message) -> Message:
        """A re-send repeats the basal (idempotent) and never the bolus."""
        bolus = self.spec.role("command_bolus")
        if bolus is None:
            return msg
        return Message(msg.spec_name, {**msg.values, bolus.field: 0.0}, msg.source_time)

    def _tell_unconfirmed(self, command_id: Any) -> None:
        """Tell the controller, in its own confirmation message, that a command went unconfirmed."""
        cid, status = self.spec.role("confirm_id"), self.spec.role("confirm_status")
        delivered = self.spec.role("confirm_delivered")
        if cid is None or status is None or delivered is None:
            return
        values = {i.field: i.lo for i in self.spec.items_of(cid.message)} | self._last_confirm
        values.update(
            {
                cid.field: command_id,
                status.field: self.spec.unconfirmed_status,
                delivered.field: 0.0,
            }
        )
        self._submit_up(Message(cid.message, values, self.sched.now))
