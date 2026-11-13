from collections import Counter

import pytest

from bridge_island.aimap import controls, evaluate, score
from bridge_island.aimap.heuristic import infer_steps
from bridge_island.aimap.task import plain_task
from bridge_island.core.elements import Representation
from bridge_island.examples import EXAMPLES


def R(enc: str, units: str) -> Representation:
    return Representation(enc, units, "f", "p")


def test_the_conversion_is_inferred_from_the_representations() -> None:
    assert infer_steps(R("float64", "m"), R("float64", "m")) == []
    assert infer_steps(R("float64", "m"), R("float64", "ft")) == [{"unit": ["m", "ft"]}]
    assert infer_steps(R("bam16", "deg"), R("float64", "rad")) == [
        {"bam_decode": 16},
        {"unit": ["deg", "rad"]},
    ]
    assert infer_steps(R("float64", "deg"), R("bam16", "deg")) == [{"bam_encode": 16}]
    old_last = [{"unit": ["ft", "m"]}, {"valid_range": [0, 1]}]
    assert infer_steps(R("float64", "ft"), R("float64", "m"), old_last) == [
        {"unit": ["ft", "m"]},
        {"valid_range": [0, 1]},
    ]  # a range check that came last stays last
    assert infer_steps(
        R("float64", "ft"), R("float64", "m"), [{"valid_range": [0, 1]}, {"unit": ["ft", "m"]}]
    )[0] == {"valid_range": [0, 1]}  # and one that came first stays first


def test_the_heuristic_handles_the_aviation_change() -> None:
    task = plain_task("aviation")
    ev = evaluate.evaluate(task, controls.heuristic_reply(task))
    assert ev.status == evaluate.ACCEPTED
    counts = Counter(r.cls for r in ev.results)
    assert set(counts) <= {score.CORRECT, score.REMOVED_HANDLED}  # renames, units, the removal
    assert ev.system_differences == ()


def test_the_heuristic_cannot_recode_an_enumeration() -> None:
    task = plain_task("medical")
    ev = evaluate.evaluate(task, controls.heuristic_reply(task))
    assert ev.status == evaluate.CONTRACT_FAILED
    assert "alarm_code" in ev.error


@pytest.mark.parametrize("example", sorted(EXAMPLES))
def test_the_heuristic_is_deterministic(example: str) -> None:
    task = plain_task(example)
    assert controls.heuristic_reply(task) == controls.heuristic_reply(task)
