"""The prompt shows the model everything it needs and none of the answer."""

import pytest

from bridge_island.aimap import prompt
from bridge_island.aimap.task import Task, plain_task, spec_text
from bridge_island.core.transforms import _BUILDERS, from_spec
from bridge_island.examples import EXAMPLES, examples_dir, load_world
from bridge_island.yamlutil import load_yaml

EXAMPLE_NAMES = sorted(EXAMPLES)


def _file(example: str, name: str) -> str:
    return (examples_dir() / EXAMPLES[example] / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("example", EXAMPLE_NAMES)
def test_the_prompt_leaks_none_of_the_answer(example: str) -> None:
    task = plain_task(example)
    # The old mapping is shown on purpose, and some new entries are a prefix of an old one.
    text = prompt.build_prompt(task).replace(prompt.v1_mapping_text(task), "")
    # Entries of the answer that differ from the old mapping, as the prompt would print them.
    changed = [e for e in task.truth["downlinks"] if e not in task.v1_spec["downlinks"]]
    forbidden = {spec_text({"x": [e]}).partition("\n")[2] for e in changed}
    # The answer's comments, the change-set notes at the top of world.py, and the rename table.
    v1_lines = {ln.strip() for ln in _file(example, "mapping_v1.yaml").splitlines()}
    forbidden |= {
        ln.strip()
        for ln in _file(example, "mapping_v2.yaml").splitlines()
        if ln.strip().startswith("#") and ln.strip() not in v1_lines
    }
    doc = load_world(example).__doc__ or ""
    forbidden |= {ln.strip() for ln in doc.splitlines() if ln.strip().startswith("*")}
    forbidden |= {"RENAMES", "change set", "mapping_v2", "removed from baseline interface v2"}
    assert len(forbidden) > 8  # the check has something to check
    assert sorted(s for s in forbidden if s in text) == []


@pytest.mark.parametrize("example", EXAMPLE_NAMES)
def test_the_prompt_contains_what_is_needed(example: str) -> None:
    task = plain_task(example)
    text = prompt.build_prompt(task)
    for msg in task.v2.published:
        assert msg.name in text
        for e in msg.elements:
            assert e.name in text
    for sub, needs in task.needs.items():
        assert sub in text
        for n in needs:
            assert n.native_name in text
    assert "Mapping for version 1" in text and "baseline_version: 1" in text
    assert prompt.build_prompt(task) == text  # no randomness, no clock


def test_the_medical_prompt_carries_the_enumeration_note() -> None:
    text = prompt.build_prompt(plain_task("medical"))
    assert "OCCLUSION" in text and "numeric codes 0 to 4" in text
    assert "OCCLUSION" not in prompt.build_prompt(plain_task("aviation"))


def test_the_vocabulary_cannot_drift_from_the_code() -> None:
    assert set(prompt.TRANSFORMS) == set(_BUILDERS)
    for key, (example, _) in prompt.TRANSFORMS.items():
        step = load_yaml(example)
        assert key in step
        from_spec(step)  # every example the prompt shows is accepted by the generator
    text = prompt.vocabulary()
    for unit in ("ft", "kPa", "mmHg", "km/h"):
        assert unit in text


@pytest.mark.parametrize("example", EXAMPLE_NAMES)
def test_the_answer_uses_only_transforms_the_prompt_teaches(example: str) -> None:
    task: Task = plain_task(example)
    used: set[str] = set()
    entries = list(task.truth.get("downlinks", []))
    for up in task.truth.get("uplinks", []):
        entries += up.get("fields", [])
    for entry in entries:
        for step in entry.get("transforms", []):
            used |= set(step) if isinstance(step, dict) else set()
    assert used and used <= set(prompt.TRANSFORMS)
