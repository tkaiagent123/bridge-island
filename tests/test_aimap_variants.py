"""Obfuscated and perturbed variants: the derived answers are right, and nothing leaks."""

from collections import Counter

import pytest

from bridge_island.aimap import controls, evaluate, prompt, score, variants
from bridge_island.aimap.task import plain_task
from bridge_island.examples import EXAMPLES

NAMES = sorted(EXAMPLES)
GOOD = {score.CORRECT, score.REMOVED_HANDLED}


@pytest.mark.parametrize("example", NAMES)
def test_the_oracle_is_perfect_on_the_obfuscated_task(example: str) -> None:
    task = variants.obfuscate(plain_task(example), seed=1)
    ev = evaluate.evaluate(task, controls.oracle(task))
    assert ev.status == evaluate.ACCEPTED
    assert {r.cls for r in ev.results} <= GOOD


@pytest.mark.parametrize("example", NAMES)
def test_obfuscation_hides_every_name_and_keeps_every_representation(example: str) -> None:
    base = plain_task(example)
    task = variants.obfuscate(base, seed=1)
    text = prompt.build_prompt(task)
    for msg in base.v2.published + base.v1.published + base.v2.accepted:
        assert msg.name not in text
        for e in msg.elements:
            assert f'"{e.name}"' not in text
    for needs in base.needs.values():
        for n in needs:
            assert n.native_name not in text
    for before, after in zip(base.v2.published, task.v2.published, strict=True):
        assert [e.representation for e in before.elements] == [
            e.representation for e in after.elements
        ]
        assert [e.valid_range for e in before.elements] == [e.valid_range for e in after.elements]
    assert task.world is None and task.variant == "obfuscated"


def test_obfuscation_is_seeded() -> None:
    base = plain_task("aviation")
    a, b, c = (variants.obfuscate(base, s) for s in (1, 1, 2))
    assert prompt.build_prompt(a) == prompt.build_prompt(b)
    assert prompt.build_prompt(a) != prompt.build_prompt(c)
    # An element that was renamed between versions keeps two unrelated tokens: the link is hidden.
    old = {f"{m.name}.{e.name}" for m in a.v1.published for e in m.elements}
    new = {f"{m.name}.{e.name}" for m in a.v2.published for e in m.elements}
    assert len(old - new) >= 2 and len(new - old) >= 2


def test_the_obfuscated_prompt_leaks_no_original_name_through_the_old_mapping() -> None:
    base = plain_task("medical")
    task = variants.obfuscate(base, seed=3)
    text = prompt.build_prompt(task)
    assert "PumpState" not in text and "volume_infused_ml" not in text
    assert "OCCLUSION" in text  # enumerated values are not names of elements and stay


@pytest.mark.parametrize("example", NAMES)
@pytest.mark.parametrize("seed", [1, 2])
def test_the_oracle_is_perfect_on_every_perturbed_case(example: str, seed: int) -> None:
    cases = variants.perturbed_tasks(plain_task(example), seed=seed, n=20)
    assert len(cases) == 20 and len({c.id for c in cases}) == 20
    for task in cases:
        ev = evaluate.evaluate(task, controls.oracle(task))
        assert ev.status == evaluate.ACCEPTED, (task.id, ev.error)
        assert {r.cls for r in ev.results} <= GOOD, task.id


def test_perturbed_cases_exercise_every_kind_of_change() -> None:
    from bridge_island.analysis.change_impact import diff_interfaces

    kinds: Counter[str] = Counter()
    for example in NAMES:
        for task in variants.perturbed_tasks(plain_task(example), seed=1, n=20):
            kinds.update(c.kind for c in diff_interfaces(task.v1, task.v2))
            kinds.update(
                ["removed_entry"]
                * sum(e.get("status") == "removed" for e in task.truth["downlinks"])
            )
    assert {"unit", "encoding", "rate", "add", "remove", "removed_entry"} <= set(kinds)


def test_perturbed_cases_are_seeded_and_differ_between_seeds() -> None:
    base = plain_task("aviation")
    a = [t.truth for t in variants.perturbed_tasks(base, 1, 10)]
    assert a == [t.truth for t in variants.perturbed_tasks(base, 1, 10)]
    assert a != [t.truth for t in variants.perturbed_tasks(base, 2, 10)]


@pytest.mark.parametrize("example", NAMES)
def test_the_perturbed_prompt_does_not_contain_the_derived_answer(example: str) -> None:
    for task in variants.perturbed_tasks(plain_task(example), seed=1, n=5):
        text = prompt.build_prompt(task).replace(prompt.v1_mapping_text(task), "")
        assert "perturbed_" not in text and "RENAMES" not in text


def test_make_tasks_dispatches_on_variant() -> None:
    base = plain_task("aviation")
    assert [t.variant for t in variants.make_tasks(base, "plain", 1, 3)] == ["plain"]
    assert [t.variant for t in variants.make_tasks(base, "obfuscated", 1, 3)] == ["obfuscated"]
    assert len(variants.make_tasks(base, "perturbed", 1, 3)) == 3
    with pytest.raises(KeyError):
        variants.make_tasks(base, "nonsense", 1, 3)
