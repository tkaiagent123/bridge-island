"""The contract monitor: every field of contract.yaml is enforced (synthetic, illustrative)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from bridge_island.bridge.contract import (
    SECTIONS,
    ContractError,
    ContractMonitor,
    ContractSpec,
    load_contract,
    parse_contract,
)
from bridge_island.clinical import loop as L
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message
from bridge_island.core.events import EventLog
from bridge_island.examples import examples_dir
from bridge_island.yamlutil import load_yaml

PATH = examples_dir() / "insulin_loop_invented" / "contract.yaml"
RAW = load_yaml(PATH.read_text(encoding="utf-8"))
SPEC = load_contract(PATH).with_failsafe_basal(0.8)


class Harness:
    def __init__(self, spec: ContractSpec = SPEC) -> None:
        self.sched = Scheduler()
        self.log = EventLog()
        self.up: list[Message] = []
        self.resent: list[Message] = []
        self.alarms: list[tuple[str, str, str]] = []
        self.mon = ContractMonitor(
            spec,
            self.sched,
            self.log,
            self.up.append,
            self.resent.append,
            lambda *a: self.alarms.append(a),
        )

    def kinds(self) -> list[str]:
        return [e.kind for e in self.log.events]

    def at(self, t: float) -> None:
        self.sched.run_until(t)
        self.sched.now = t


def glucose(value: float = 120.0, status: int = 0, t: float = 0.0) -> Message:
    return Message(
        "GlucoseReading", {"glucose": value, "sensor_status": status, "source_time": t}, t
    )


def command(cid: int, t: float, basal: float = 1.0, bolus: float = 0.0) -> Message:
    values = {"command_id": cid, "basal": basal, "bolus": bolus, "valid_until": t + 720}
    return Message("DoseCommand", values, t)


def confirm(cid: int, status: int = 0) -> Message:
    values = {"command_id": cid, "delivered": 0.0, "pump_status": status, "reservoir": 200.0}
    return Message("DeliveryConfirmation", values | {"battery": 80}, 0.0)


# ---- the file: eight sections, each checked ----------------------------------------------


def test_the_shipped_contract_has_all_eight_sections() -> None:
    assert set(RAW) == set(SECTIONS)
    assert SPEC.owner and SPEC.regulatory_home  # 1
    assert len(SPEC.items) >= 10 and SPEC.role("glucose_value") is not None  # 2
    assert SPEC.max_age_s["GlucoseReading"] == 660 and SPEC.latency_budget_s == 60  # 3
    assert SPEC.confirm_max_resends == 2 and SPEC.alarm_target == "CompanionDisplay"  # 4
    assert "DoseCommand" in SPEC.down and "SensorB" in SPEC.sources["GlucoseReading"]  # 5
    assert [c.id for c in SPEC.claims] == ["C1", "C2", "C3", "C4", "C5", "C6"]  # 6
    assert "mmol/L" in SPEC.change_envelope["glucose_units"]  # 7
    assert len(SPEC.residual) >= 4  # 8


def _broken(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(RAW)
    node = data
    for key in path[:-1]:
        node = node[key]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return data


@pytest.mark.parametrize(
    "path, value",
    [
        (("owner",), ""),
        (("regulatory_home",), KeyError),
        (("data_items",), []),
        (("data_items", 0, "range"), [400, 40]),
        (("data_items", 0, "role"), "nonsense"),
        (("data_items", 0, "name"), "no_dot"),
        (("timing", "max_age_s", "GlucoseReading"), -1),
        (("timing", "check_period_s"), 0),
        (("failure_behavior", "failsafe_basal_u_h"), "lots"),
        (("failure_behavior", "ok_statuses"), []),
        (("failure_behavior", "confirmation", "max_resends"), -1),
        (("failure_behavior", "alarm_delivery", "target"), ""),
        (("security", "sources", "GlucoseReading"), []),
        (("security", "sources", "Unknown"), ["X"]),
        (("claims",), []),
        (("claims", 0, "tests"), []),
        (("change_envelope",), "anything"),
        (("residual",), []),
        (("extra",), 1),
    ],
)
def test_every_section_is_validated(path: tuple[Any, ...], value: Any) -> None:
    with pytest.raises(ContractError):
        parse_contract(_broken(path, value))


def test_a_role_may_be_given_once_and_the_glucose_roles_are_required() -> None:
    data = copy.deepcopy(RAW)
    data["data_items"][1]["role"] = "glucose_value"
    with pytest.raises(ContractError, match="one item only"):
        parse_contract(data)
    data = copy.deepcopy(RAW)
    data["data_items"][2].pop("role")
    with pytest.raises(ContractError, match="glucose_time"):
        parse_contract(data)


def test_an_uninstalled_contract_cannot_run_and_a_bad_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="install"):
        ContractMonitor(load_contract(PATH), Scheduler(), EventLog(), print, print, print)
    bad = tmp_path / "c.yaml"
    bad.write_text("a: [", encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract(bad)


# ---- 2 and 3: data items and timing ------------------------------------------------------


def test_good_glucose_passes_and_is_remembered() -> None:
    h = Harness()
    out = h.mon.check_up("SensorA", glucose(120.0, 0, 0.0))
    assert out is not None and out.values["glucose"] == 120.0
    assert h.mon.last_glucose_time == 0.0 and not h.mon.failsafe


def test_out_of_range_glucose_is_flagged() -> None:
    """Claim C4: an out-of-range or error reading reaches the controller only flagged."""
    for bad in (glucose(600.0), glucose(120.0, status=3), glucose(float("nan"))):
        h = Harness()
        out = h.mon.check_up("SensorA", bad)
        assert out is not None
        assert out.values["sensor_status"] == SPEC.failsafe_status
        assert out.values["glucose"] == 0.0
        assert h.kinds()[:2] == ["contract.failsafe", "contract.alarm"]
        assert h.alarms and h.alarms[0][0] == "CompanionDisplay"
    h = Harness()
    h.mon.rejected_up("SensorB", "reading", "600 outside [40, 400]")
    assert h.up and h.up[0].values["sensor_status"] == SPEC.failsafe_status
    h.mon.rejected_up("CompanionDisplay", "ack", "bad")  # not a glucose source: no fail-safe
    assert len(h.up) == 1


def test_low_and_high_readings_are_valid_statuses() -> None:
    h = Harness()
    assert h.mon.check_up("SensorA", glucose(40.0, 1))
    assert h.mon.check_up("SensorA", glucose(400.0, 2))
    assert not h.mon.failsafe


def test_old_glucose_is_flagged_and_fresh_glucose_recovers() -> None:
    h = Harness()
    h.at(1000.0)
    out = h.mon.check_up("SensorA", glucose(120.0, 0, 1000.0 - 700))
    assert out is not None and out.values["sensor_status"] == SPEC.failsafe_status
    assert h.mon.failsafe
    h.mon.check_up("SensorA", glucose(130.0, 0, 1000.0 - 800))  # still old: no second alarm
    assert h.kinds().count("contract.alarm") == 1 and "contract.violation" in h.kinds()
    fresh = h.mon.check_up("SensorA", glucose(130.0, 0, 1000.0))
    assert fresh is not None and h.mon.failsafe  # recovery waits for the controller to get it
    h.mon.handover(fresh)
    assert not h.mon.failsafe and "contract.recovered" in h.kinds()


def test_missing_and_stale_glucose_are_found_by_the_periodic_check() -> None:
    h = Harness()
    h.at(560.0)
    h.mon.scan()
    assert not h.up  # flagged at 660 - 90 = 570 s
    h.at(580.0)
    h.mon.scan()
    assert h.up[-1].values["sensor_status"] == SPEC.failsafe_status
    assert "no glucose" in h.log.of_kind("contract.failsafe")[0].detail
    h = Harness()
    h.mon.handover(glucose(120.0, 0, 0.0))
    h.at(571.0)
    h.mon.scan()
    assert "is stale" in h.log.of_kind("contract.failsafe")[0].detail


# ---- 4: failure behaviour ----------------------------------------------------------------


def test_commands_carry_the_failsafe_basal_while_glucose_is_lost() -> None:
    h = Harness()
    h.mon.check_up("SensorA", glucose(600.0))
    out = h.mon.check_down(command(1, 0.0, basal=3.0, bolus=2.0))
    assert out is not None and out.values["basal"] == 0.8 and out.values["bolus"] == 2.0
    assert "contract.failsafe_basal" in h.kinds()


def test_unconfirmed_command_retry_then_alarm() -> None:
    """Claim C5: re-sent at most twice (never with the bolus), then alarmed, controller told."""
    h = Harness()
    h.mon.check_up("PumpA", confirm(0))  # remember the pump's last report
    assert h.mon.check_down(command(7, 0.0, bolus=2.0)) is not None
    for t in (60, 120, 180, 240, 300, 360, 420):
        h.at(float(t))
        h.mon.scan()
    assert len(h.resent) == 2 and all(m.values["bolus"] == 0.0 for m in h.resent)
    assert h.resent[0].values["command_id"] == 7
    assert h.kinds().count("contract.resend") == 2
    assert "contract.unconfirmed" in h.kinds()
    told = [m for m in h.up if m.spec_name == "DeliveryConfirmation"]
    assert told and told[0].values["pump_status"] == SPEC.unconfirmed_status
    assert set(told[0].values) == {"command_id", "delivered", "pump_status", "reservoir", "battery"}
    assert any("not confirmed" in a[2] for a in h.alarms)


def test_a_confirmation_clears_the_pending_command() -> None:
    h = Harness()
    h.mon.check_down(command(3, 0.0))
    assert h.mon.check_up("PumpB", confirm(3)) is not None
    h.at(1000.0)
    h.mon.scan()
    assert not h.resent and "contract.confirmed" in h.kinds()


def test_the_controller_is_told_even_before_any_confirmation() -> None:
    h = Harness()
    h.mon.check_down(command(1, 0.0))
    for t in (200, 400, 600):
        h.at(float(t))
        h.mon.scan()
    told = [m for m in h.up if m.spec_name == "DeliveryConfirmation"]
    assert told and told[0].values["reservoir"] == 0  # filled from the item's lower limit


def test_alarms_are_resent_then_logged_undelivered_with_their_age() -> None:
    h = Harness()
    alarm = h.mon.raise_alarm("test", "something")
    for t in (130, 250, 370, 500):
        h.at(float(t))
        h.mon.scan()
    assert len(h.alarms) == 3  # the alarm and two re-sends
    undelivered = h.log.of_kind("contract.alarm_undelivered")
    assert len(undelivered) == 1 and "age 370 s" in undelivered[0].detail
    assert alarm.undelivered


def test_an_acknowledged_alarm_is_not_resent() -> None:
    h = Harness()
    alarm = h.mon.raise_alarm("test", "x")
    out = h.mon.check_up("CompanionDisplay", Message("AlarmAck", {"alarm_id": alarm.id}, 1.0))
    assert out is not None and alarm.acked_at == 0.0
    h.at(1000.0)
    h.mon.scan()
    assert sum(1 for a in h.alarms if a[2].startswith("#1 ")) == 1  # never re-sent
    assert "contract.ack" in h.kinds()


def test_a_new_controller_alarm_is_forwarded_once() -> None:
    h = Harness()
    status = {"glucose": 60.0, "alarm_code": 1, "loop_mode": 2, "iob": 0.0}
    for _ in range(3):
        assert h.mon.check_down(Message("ControllerStatus", status, 0.0)) is not None
    assert [a[2] for a in h.alarms] == ["#1 host_alarm: controller alarm code 1"]


# ---- 5: security -------------------------------------------------------------------------


def test_security_blocks_unlisted_sender_and_direction() -> None:
    """Claim C6."""
    h = Harness()
    assert h.mon.check_up("PumpA", glucose()) is None  # a pump may not send glucose
    assert h.mon.check_up("SensorA", Message("SetBasal", {}, 0.0)) is None
    assert h.mon.check_down(Message("GlucoseReading", {}, 0.0)) is None  # wrong direction
    assert h.mon.blocked == 3 and h.kinds().count("contract.block") == 3


def test_replays_and_expired_commands_are_blocked() -> None:
    h = Harness()
    assert h.mon.check_down(command(5, 0.0, bolus=3.0)) is not None
    assert h.mon.check_down(command(5, 0.0, bolus=3.0)) is None
    h.at(800.0)
    assert h.mon.check_down(command(6, 0.0)) is None  # valid until 720 s
    assert h.kinds().count("contract.replay_blocked") == 2


def test_out_of_range_items_are_refused_both_ways() -> None:
    h = Harness()
    assert h.mon.check_down(command(1, 0.0, bolus=40.0)) is None
    assert h.mon.check_up("PumpA", confirm(1, status=9)) is None
    assert h.kinds().count("contract.violation") == 2


# ---- 6 and 8: claims and residual are shown, and each claim names a real test ---------------


def test_every_claim_names_a_test_that_exists() -> None:
    root = Path(__file__).resolve().parent.parent
    for claim in SPEC.claims:
        for ref in claim.tests:
            file, _, name = ref.partition("::")
            assert f"def {name}(" in (root / file).read_text(encoding="utf-8"), ref


def test_the_contract_is_installed_with_the_patients_programmed_basal() -> None:
    loop = L.build_loop("bridge-contract", L.patients(1)[2])
    assert loop.bridge is not None and loop.bridge.contract is not None
    assert loop.bridge.contract.spec.failsafe_basal_u_h == L.patients(1)[2].basal_u_h
    assert loop.bridge.partitions["contract"] == "contract-monitor"
    basic = L.build_loop("bridge-basic", L.patients(1)[2])
    assert basic.bridge is not None and basic.bridge.contract is None
    assert "contract" not in basic.bridge.partitions
