"""The scorer: the oracle is perfect, and each way of being wrong is classified as such."""

import copy
from collections import Counter
from typing import Any

import pytest
import yaml

from bridge_island.aimap import controls, evaluate, score
from bridge_island.aimap.parse import entry_ids
from bridge_island.aimap.task import Task, plain_task
from bridge_island.examples import EXAMPLES

NAMES = sorted(EXAMPLES)


def _reply(
    spec: dict[str, Any], levels: dict[str, str] | None = None, default: str = "high"
) -> str:
    levels = levels or {}
    items = [
        {"entry": e, "confidence": levels.get(e, default), "reason": "test"}
        for e in entry_ids(spec)
    ]
    return yaml.safe_dump({"mapping": spec, "confidence": items}, sort_keys=False)


def _truth(task: Task) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in task.truth.items() if k != "world"}


def _classes(task: Task, reply: str) -> Counter[str]:
    return Counter(r.cls for r in evaluate.evaluate(task, reply).results)


@pytest.mark.parametrize("example", NAMES)
def test_the_oracle_scores_one_hundred_percent(example: str) -> None:
    task = plain_task(example)
    ev = evaluate.evaluate(task, controls.oracle(task))
    assert ev.status == evaluate.ACCEPTED
    counts = Counter(r.cls for r in ev.results)
    assert set(counts) <= {score.CORRECT, score.REMOVED_HANDLED}
    assert counts[score.REMOVED_HANDLED] == 1  # the one element each example removes
    assert ev.system_differences == ()  # the system run agrees with the transform-level scoring


@pytest.mark.parametrize("example", NAMES)
def test_carry_forward_is_rejected_by_the_contract(example: str) -> None:
    task = plain_task(example)
    ev = evaluate.evaluate(task, controls.carry_forward(task))
    assert ev.status == evaluate.CONTRACT_FAILED and ev.error
    assert {r.cls for r in ev.results} == {score.REJECTED}
    assert len(ev.results) == len(controls.oracle(task).split("entry:")) - 1  # one per entry


def test_unparseable_replies_are_their_own_class() -> None:
    task = plain_task("aviation")
    ev = evaluate.evaluate(task, "I would rather not.")
    assert ev.status == evaluate.PARSE_FAILED
    assert {r.cls for r in ev.results} == {score.UNPARSEABLE}


def _first_unit_entry(spec: dict[str, Any]) -> dict[str, Any]:
    return next(e for e in spec["downlinks"] if any("unit" in t for t in e.get("transforms", [])))


def test_a_different_spelling_of_the_same_conversion_is_correct() -> None:
    task = plain_task("aviation")
    spec = _truth(task)
    entry = _first_unit_entry(spec)  # knots to km/h, written as two steps through m/s
    entry["transforms"] = [{"unit": ["kt", "m/s"]}, {"unit": ["m/s", "km/h"]}]
    counts = _classes(task, _reply(spec))
    assert set(counts) <= {score.CORRECT, score.REMOVED_HANDLED}
    entry["transforms"] = [{"unit": ["kt", "m/s"]}]  # the target no longer gets its units
    assert evaluate.evaluate(task, _reply(spec)).status == evaluate.CONTRACT_FAILED


def test_a_proposal_that_passes_the_contract_but_behaves_differently() -> None:
    """The case the experiment exists for: accepted by every check, still wrong."""
    task = plain_task("aviation")
    spec = _truth(task)
    lat = next(e for e in spec["downlinks"] if e["to"] == "PlanningDisplay.nav_lat_deg")
    del lat["transforms"]  # drops the range check: the contract allows it, behaviour differs
    key = f"{lat['from']} -> {lat['to']}"
    high = _classes(task, _reply(spec))
    assert high[score.WRONG_UNFLAGGED] == 1 and high[score.WRONG_FLAGGED] == 0
    low = _classes(task, _reply(spec, {key: "low"}))
    assert low[score.WRONG_FLAGGED] == 1 and low[score.WRONG_UNFLAGGED] == 0
    unrated = evaluate.evaluate(task, yaml.safe_dump({"mapping": spec}))
    assert (
        Counter(r.cls for r in unrated.results)[score.WRONG_UNFLAGGED] == 1
    )  # no rating = unflagged


def test_a_different_source_element_is_wrong() -> None:
    task = plain_task("aviation")
    spec = _truth(task)
    entry = next(e for e in spec["downlinks"] if e["to"] == "CueingProcessor.pos_alt_m")
    entry["from"] = "AirData.airspeed_kt"  # fits the contract only if units match: it does not
    assert evaluate.evaluate(task, _reply(spec)).status == evaluate.CONTRACT_FAILED
    entry = next(e for e in spec["downlinks"] if e["to"] == "PlanningDisplay.nav_alt_m")
    entry["from"] = "NavigationState.altitude"
    assert _classes(task, _reply(spec))[score.WRONG_UNFLAGGED] == 0  # unchanged entry is correct


def test_a_guessed_substitute_for_the_removed_element() -> None:
    task = plain_task("aviation")
    spec = _truth(task)
    removed = next(e for e in spec["downlinks"] if e.get("status") == "removed")
    removed.clear()
    removed.update(
        {"from": "NavigationState.altitude", "to": "CueingProcessor.baro_alt_m"}
    )  # a plausible guess that fits the contract
    counts = _classes(task, _reply(spec))
    assert counts[score.REMOVED_GUESSED] == 1 and counts[score.REMOVED_HANDLED] == 0


def test_an_omitted_uplink_is_missing() -> None:
    task = plain_task("aviation")
    spec = _truth(task)
    del spec["uplinks"]
    counts = _classes(task, _reply(spec))
    assert counts[score.MISSING] == 2  # the two fields of the one uplink message


def test_an_uplink_with_swapped_fields_is_wrong() -> None:
    task = plain_task("aviation")
    spec = _truth(task)
    fields = spec["uplinks"][0]["fields"]
    fields[0]["to"], fields[1]["to"] = fields[1]["to"], fields[0]["to"]
    ev = evaluate.evaluate(task, _reply(spec))
    if ev.status == evaluate.ACCEPTED:
        assert Counter(r.cls for r in ev.results)[score.WRONG_UNFLAGGED] == 2
    else:  # equal representations on both fields make the swap invisible to the contract
        assert ev.status == evaluate.CONTRACT_FAILED


def test_enumerated_values_are_compared_by_what_they_map_to() -> None:
    task = plain_task("medical")
    spec = _truth(task)
    code = next(e for e in spec["downlinks"] if e["to"] == "BedsideDisplay.alarm_code")
    enum = code["transforms"][0]["enum"]
    enum["map"] = {**enum["map"], "OCCLUSION": 2, "AIR_IN_LINE": 1}  # two codes swapped
    assert _classes(task, _reply(spec))[score.WRONG_UNFLAGGED] == 1
    assert _classes(task, _reply(_truth(task)))[score.WRONG_UNFLAGGED] == 0
