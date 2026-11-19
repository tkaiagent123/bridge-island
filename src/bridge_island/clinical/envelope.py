"""The change envelope: is a declared peripheral change one the Bridge absorbs on its own?

``check`` reads a change file (``examples/insulin_loop_invented/changes/*.yaml``) and compares what
it declares with the contract's ``change_envelope``. Inside the envelope, the answer lists the
conformance tests to re-run, all of which exercise the Bridge only. Outside, it lists which island
owner must review, and why.

The conformance suite is generated from the contract: one test per contract field (units, range,
encoding) per data item, one per timing limit, and one per claim. ``verify`` runs them on short
bridge-contract runs. Synthetic, illustrative. It computes no regulatory outcome; an envelope with
conformance tests is offered as an engineering basis that is consistent with how a predetermined
change plan is described, nothing more.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bridge_island.bridge.contract import ContractSpec, DataItem
from bridge_island.clinical import loop as L
from bridge_island.clinical.guidelines import visibility_problems
from bridge_island.core.elements import Message
from bridge_island.scenario import ScenarioError
from bridge_island.yamlutil import YamlError, load_yaml

SIDES = ("unique", "baseline")
DECLARES = {
    "glucose_units",
    "sample_period_s",
    "sensor_lag_s",
    "protocol",
    "field_renames",
    "bolus_resolution_u",
    "display_latency_s",
    "baseline_version",
    "new_crossing_fields",
}
INTEGRATOR = "Integrator Org (invented)"


@dataclass(frozen=True)
class Change:
    name: str
    description: str
    side: str
    device: str
    declares: dict[str, Any]
    simulate: str = (
        "default"  # which device set the conformance runs use: default, sensor_swap, pump_swap
    )
    path: Path | None = None


@dataclass
class Decision:
    change: Change
    inside: bool
    reasons: list[str] = field(default_factory=list)  # why it is outside (empty if inside)
    tests: list[str] = field(default_factory=list)  # conformance tests to re-run (inside)
    owners: list[tuple[str, str]] = field(default_factory=list)  # (organization, why) to involve

    def as_dict(self) -> dict[str, Any]:
        return {
            "change": self.change.name,
            "device": self.change.device,
            "side": self.change.side,
            "inside_envelope": self.inside,
            "reasons": self.reasons,
            "tests_to_rerun": self.tests,
            "owners_to_involve": [{"organization": o, "why": w} for o, w in self.owners],
            "note": "synthetic, illustrative; not a regulatory determination",
        }


# ---- change files --------------------------------------------------------------------------


def changes_dir() -> Path:
    return Path(L.world().HERE) / "changes"


def parse_change(data: Any, source: str = "change") -> Change:
    if not isinstance(data, dict):
        raise ScenarioError(f"{source}: expected a mapping")
    unknown = sorted(set(data) - {"name", "description", "side", "device", "declares", "simulate"})
    if unknown:
        raise ScenarioError(f"{source}: unknown key(s) {unknown}")
    if data.get("side") not in SIDES:
        raise ScenarioError(f"{source}: side must be one of {list(SIDES)}")
    owners = L.world().OWNERS
    if data.get("device") not in owners:
        raise ScenarioError(f"{source}: device must be one of {sorted(owners)}")
    declares = data.get("declares") or {}
    if not isinstance(declares, dict) or set(declares) - DECLARES:
        raise ScenarioError(f"{source}: declares may hold only {sorted(DECLARES)}")
    simulate = data.get("simulate", "default")
    if simulate not in L.DEVICE_SETS:
        raise ScenarioError(f"{source}: simulate must be one of {sorted(L.DEVICE_SETS)}")
    return Change(
        str(data.get("name", Path(source).stem)),
        str(data.get("description", "")),
        data["side"],
        data["device"],
        dict(declares),
        simulate,
    )


def load_change(path: Path) -> Change:
    try:
        data = load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ScenarioError(f"cannot read {path}: {exc}") from exc
    except YamlError as exc:
        raise ScenarioError(f"{path}: not valid YAML: {exc}") from exc
    change = parse_change(data, str(path))
    return Change(**{**vars(change), "path": path})


def shipped_changes() -> list[Change]:
    return [load_change(p) for p in sorted(changes_dir().glob("*.yaml"))]


# ---- the conformance suite -----------------------------------------------------------------

Check = Callable[[str], str | None]  # device set -> problem, or None


@dataclass(frozen=True)
class Conformance:
    name: str  # e.g. "item/GlucoseReading.glucose/range"
    touches: tuple[str, ...]  # the messages it exercises
    check: Check


@functools.lru_cache(maxsize=8)
def _observed(devices: str) -> L.Loop:
    """One hour of bridge-contract traffic for a device set, shared by the item checks."""
    faults = {
        "default": (),
        "sensor_swap": (L.Fault("sensor_swap", "00:00"),),
        "pump_swap": (L.Fault("pump_swap", "00:00"),),
    }[devices] + (
        L.Fault("sensor_dropout", "02:00", 20),
    )  # one alarm, so an acknowledgment crosses
    scenario = L.LoopScenario("conformance", duration_s=8 * 3600.0, faults=faults)
    loop = L.build_loop("bridge-contract", L.patients(1)[0], scenario)
    loop.run()
    return loop


def _crossing(loop: L.Loop, item: DataItem) -> list[tuple[float, Any]]:
    """Every value of one item that crossed: accepted by the host, or executed by a pump."""
    if item.message in {s.name for s in L.world().interface(1).accepted}:
        return [
            (t, m.values[item.field])
            for t, m in loop.platform.accepted
            if m.spec_name == item.message and m.values.get("sensor_status") != 4
        ]
    if item.message == "DoseCommand":
        sent = loop.controller.sent_values
        return [
            (t, sent[cid][item.field])
            for name in loop.devices
            if name.startswith("Pump")
            for t, cid, _, _ in loop.pump(name).executed
            if cid in sent
        ]
    return [
        (t, v[item.field]) for t, v in loop.controller.outputs("status")
    ]  # as the host published


def _item_check(item: DataItem, aspect: str) -> Check:
    def check(devices: str) -> str | None:
        loop = _observed(devices)
        values = _crossing(loop, item)
        if not values:
            return f"no {item.name} crossed in the conformance run"
        if aspect == "range":
            bad = [
                v
                for _, v in values
                if item.violation(v) is not None and "integer" not in str(item.violation(v))
            ]
            return f"{len(bad)} values outside [{item.lo:g}, {item.hi:g}]" if bad else None
        if aspect == "encoding":
            want_int = item.encoding.startswith(("uint", "int"))
            bad = [
                v
                for _, v in values
                if isinstance(v, bool) or (want_int and not float(v).is_integer())
            ]
            return f"{len(bad)} values not {item.encoding}" if bad else None
        if (
            item.name == "GlucoseReading.glucose"
        ):  # units: the host sees what the sensor meant, in mg/dL
            meant = {
                t: v
                for n in loop.devices
                if n.startswith("Sensor")
                for t, v, _ in loop.sensor(n).sent
            }
            off = [
                m.values["glucose"]
                for _, m in loop.platform.accepted
                if m.spec_name == "GlucoseReading"
                and m.values["sensor_status"] == 0
                and abs(
                    m.values["glucose"] - meant.get(m.values["source_time"], m.values["glucose"])
                )
                > 5
            ]
            return f"{len(off)} readings not in {item.units}" if off else None
        if item.name == "DoseCommand.bolus":  # units: what the pump delivered is what was commanded
            issued = loop.controller.issued
            off = [
                cid
                for n in loop.devices
                if n.startswith("Pump")
                for _, cid, bolus, _ in loop.pump(n).executed
                if cid in issued and abs(bolus - issued[cid][2]) > 0.05 and bolus > 0
            ]
            return f"{len(off)} boluses not delivered in {item.units}" if off else None
        return None

    return check


def _timing_check(message: str, limit: float) -> Check:
    def check(devices: str) -> str | None:
        loop = _observed(devices)
        ages = [
            t - m.values["source_time"]
            for t, m in loop.platform.accepted
            if m.spec_name == message and m.values["sensor_status"] != 4
        ]
        late = [a for a in ages if a > limit]
        return f"{len(late)} readings older than {limit:g} s at the host" if late else None

    return check


def _claim_runs() -> dict[str, Check]:
    def short(faults: tuple[L.Fault, ...], devices: str, hours: float = 5.0) -> L.Loop:
        swap = {
            "sensor_swap": (L.Fault("sensor_swap", "00:00"),),
            "pump_swap": (L.Fault("pump_swap", "00:00"),),
        }
        all_faults = swap.get(devices, ()) + faults
        loop = L.build_loop(
            "bridge-contract",
            L.patients(1)[0],
            L.LoopScenario("claim", duration_s=hours * 3600.0, faults=all_faults),
        )
        loop.run()
        return loop

    def c1(devices: str) -> str | None:
        loop = short(
            (L.Fault("sensor_dropout", "01:00", 45), L.Fault("sensor_delay", "03:00", 60)), devices
        )
        old = [
            d
            for d in loop.controller.decisions
            if d.glucose is not None and (d.glucose_age_s or 0) > 660
        ]
        return f"{len(old)} cycles used unflagged stale glucose" if old else None

    def c2(devices: str) -> str | None:
        loop = short((L.Fault("replay_command", "07:00", 0, (("delay_min", 7),)),), devices, 10.0)
        counts: dict[Any, int] = {}
        for n in (d for d in loop.devices if d.startswith("Pump")):
            for _, cid, bolus, _ in loop.pump(n).executed:
                counts[cid] = counts.get(cid, 0) + (bolus > 0)
        return "a bolus was delivered twice" if max(counts.values(), default=0) > 1 else None

    def c3(devices: str) -> str | None:
        loop = short(
            (L.Fault("sensor_dropout", "01:00", 45), L.Fault("display_throttle", "00:55", 90)),
            devices,
        )
        problems = visibility_problems(loop)
        return "; ".join(problems) if problems else None

    def c4(devices: str) -> str | None:
        loop = short(
            (L.Fault("sensor_value", "01:00", 0, (("readings", 2), ("value", 600))),), devices
        )
        leaked = [
            m
            for _, m in loop.platform.accepted
            if m.spec_name == "GlucoseReading"
            and m.values["glucose"] > 400
            and m.values["sensor_status"] != 4
        ]
        return (
            f"{len(leaked)} out-of-range readings reached the controller unflagged"
            if leaked
            else None
        )

    def c5(devices: str) -> str | None:
        loop = short((L.Fault("pump_silent", "01:00", 60),), devices)
        assert loop.bridge is not None
        per: dict[str, int] = {}
        for e in loop.bridge.log.of_kind("contract.resend"):
            cid = e.detail.split(" ")[1]
            per[cid] = per.get(cid, 0) + 1
        told = [
            m
            for _, m in loop.platform.accepted
            if m.spec_name == "DeliveryConfirmation" and m.values["pump_status"] == 3
        ]
        if not told:
            return "the controller was never told about an unconfirmed command"
        worst = max(per.values(), default=0)
        return f"a command was re-sent {worst} times, limit 2" if worst > 2 else None

    def c6(devices: str) -> str | None:
        loop = short((), devices, 1.0)
        assert loop.bridge is not None
        reading = Message("reading", {"glucose": 100.0, "status": 0, "sample_time": 0.0}, 0.0)
        before = len(loop.platform.accepted)
        loop.bridge._on_unique("PumpA", reading)  # a pump trying to send glucose
        loop.system.run(loop.scenario.duration_s + 120.0)
        contract_blocked = loop.bridge.contract.blocked if loop.bridge.contract else 0
        blocked = loop.bridge.cyber.blocked or contract_blocked
        return None if blocked else f"accepted {len(loop.platform.accepted) - before}"

    return {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6}


CLAIM_TOUCHES = {
    "C1": ("GlucoseReading",),
    "C2": ("DoseCommand", "DeliveryConfirmation"),
    "C3": ("ControllerStatus", "AlarmAck", "GlucoseReading"),
    "C4": ("GlucoseReading",),
    "C5": ("DoseCommand", "DeliveryConfirmation"),
    "C6": ("GlucoseReading", "DeliveryConfirmation", "AlarmAck"),
}


def suite(spec: ContractSpec | None = None) -> list[Conformance]:
    """The conformance suite generated from the contract."""
    spec = spec or L.contract_spec()
    out = [
        Conformance(f"item/{item.name}/{aspect}", (item.message,), _item_check(item, aspect))
        for item in spec.items
        for aspect in ("units", "range", "encoding")
    ]
    out += [
        Conformance(f"timing/{msg}/max_age", (msg,), _timing_check(msg, limit))
        for msg, limit in spec.max_age_s.items()
    ]
    claims = _claim_runs()
    out += [Conformance(f"claim/{c.id}", CLAIM_TOUCHES[c.id], claims[c.id]) for c in spec.claims]
    return out


def verify(
    names: list[str] | None = None, devices: str = "default"
) -> list[tuple[str, str | None]]:
    """Run conformance tests (all, or the named ones): (name, problem or None)."""
    chosen = [c for c in suite() if names is None or c.name in names]
    return [(c.name, c.check(devices)) for c in chosen]


# ---- the decision --------------------------------------------------------------------------

DEVICE_MESSAGES = {
    "SensorA": ("GlucoseReading",),
    "SensorB": ("GlucoseReading",),
    "PumpA": ("DoseCommand", "DeliveryConfirmation"),
    "PumpB": ("DoseCommand", "DeliveryConfirmation"),
    "CompanionDisplay": ("ControllerStatus", "AlarmAck"),
    "ControllerHost": (
        "GlucoseReading",
        "DeliveryConfirmation",
        "AlarmAck",
        "DoseCommand",
        "ControllerStatus",
    ),
}


def check(change: Change, spec: ContractSpec | None = None) -> Decision:
    spec = spec or L.contract_spec()
    env = spec.change_envelope
    owners = L.world().OWNERS
    d = change.declares
    reasons: list[tuple[str, str]] = []  # (who must review, why)
    host = owners["ControllerHost"]
    device = owners[change.device]
    if change.side == "baseline" or "baseline_version" in d:
        version = d.get("baseline_version")
        if version not in env.get("baseline_versions", []):
            reasons.append(
                (
                    host,
                    f"the controller host's interface changes (baseline v{version}); the host owner changes and re-verifies the host",  # noqa: E501
                )
            )
            reasons.append(
                (
                    INTEGRATOR,
                    "the contract's data items are written for baseline v1 and must be re-issued",
                )
            )
    if "glucose_units" in d and d["glucose_units"] not in env.get("glucose_units", []):
        reasons.append(
            (
                INTEGRATOR,
                f"glucose units {d['glucose_units']!r} are not in the envelope {env.get('glucose_units')}",  # noqa: E501
            )
        )
        reasons.append((host, "a new glucose unit is a new input form for the controller host"))
    if "sample_period_s" in d:
        lo, hi = env.get("sample_period_s", [0, 0])
        if not lo <= d["sample_period_s"] <= hi:
            reasons.append(
                (INTEGRATOR, f"sample period {d['sample_period_s']} s is outside [{lo}, {hi}] s")
            )
            reasons.append((host, "the controller's cycle assumes readings at least this often"))
    if "sensor_lag_s" in d and d["sensor_lag_s"] > env.get("sensor_lag_s_max", 0):
        reasons.append(
            (
                INTEGRATOR,
                f"sensor lag {d['sensor_lag_s']} s exceeds the envelope's {env.get('sensor_lag_s_max')} s",  # noqa: E501
            )
        )
        reasons.append(
            (host, "the controller's tuning has not been shown to tolerate this much lag")
        )
    if "protocol" in d and d["protocol"] not in env.get("protocols", []):
        reasons.append((INTEGRATOR, f"protocol {d['protocol']!r} has no UID port"))
    if "bolus_resolution_u" in d and d["bolus_resolution_u"] not in env.get(
        "bolus_resolution_u", []
    ):
        reasons.append(
            (INTEGRATOR, f"bolus resolution {d['bolus_resolution_u']} U is not in the envelope")
        )
    if "display_latency_s" in d and d["display_latency_s"] > env.get("display_latency_s_max", 0):
        reasons.append(
            (
                INTEGRATOR,
                f"display latency {d['display_latency_s']} s exceeds {env.get('display_latency_s_max')} s",  # noqa: E501
            )
        )
    if d.get("new_crossing_fields"):
        reasons.append(
            (
                host,
                f"new data must cross the boundary ({', '.join(d['new_crossing_fields'])}): the host must publish or accept it",  # noqa: E501
            )
        )
        reasons.append((INTEGRATOR, "a new crossing item needs a new contract data item"))
    if reasons:
        involve = list(dict.fromkeys([*(o for o, _ in reasons), device]))
        why = {o: "; ".join(w for oo, w in reasons if oo == o) for o in involve}
        why[device] = why.get(device) or "owns the changed device"
        return Decision(change, False, [w for _, w in reasons], [], [(o, why[o]) for o in involve])
    touched = set(DEVICE_MESSAGES[change.device])
    tests = [c.name for c in suite(spec) if touched & set(c.touches)]
    return Decision(
        change,
        True,
        [],
        tests,
        [
            (INTEGRATOR, "updates the mapping and re-runs the listed conformance tests"),
            (device, "owns the changed device"),
        ],
    )
