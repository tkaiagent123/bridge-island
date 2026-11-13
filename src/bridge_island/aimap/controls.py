"""Controls that give the model's numbers something to be compared with.

* ``oracle`` returns the hand-written mapping: it must score 100%, which tests the scorer.
* ``carry_forward`` returns the old mapping relabelled as the new one: the "do nothing" baseline.
* ``heuristic`` is the non-AI baseline (see ``heuristic.py``).

Each control returns a reply in the same format a model is asked for, so one pipeline scores all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from bridge_island.aimap.parse import entry_ids
from bridge_island.aimap.task import Task

CONTROLS = ("oracle", "carry_forward", "heuristic")


def reply_text(spec: Mapping[str, Any], confidence: Mapping[str, tuple[str, str]]) -> str:
    """A reply in the requested format: the mapping and a confidence for each entry."""
    items = [
        {
            "entry": e,
            "confidence": confidence.get(e, ("high", ""))[0],
            "reason": confidence.get(e, ("", "control"))[1],
        }
        for e in entry_ids(spec)
    ]
    return yaml.safe_dump({"mapping": dict(spec), "confidence": items}, sort_keys=False, width=100)


def _without_world(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in spec.items() if k != "world"}


def oracle(task: Task) -> str:
    return reply_text(_without_world(task.truth), {})


def carry_forward(task: Task) -> str:
    spec = _without_world(task.v1_spec)
    spec["name"] = f"{task.truth.get('name', 'mapping')}"
    spec["baseline_version"] = 2
    return reply_text(spec, {})


def heuristic_reply(task: Task) -> str:
    from bridge_island.aimap.heuristic import heuristic  # imports this module, so load it late

    return heuristic(task)


def control_reply(name: str, task: Task) -> str:
    replies = {"oracle": oracle, "carry_forward": carry_forward, "heuristic": heuristic_reply}
    return replies[name](task)
