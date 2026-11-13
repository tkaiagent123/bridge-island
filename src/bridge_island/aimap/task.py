"""One mapping task: the old and new baseline interface, what the systems need, and the answer.

``truth`` is the hand-written mapping for the new version. It is used only for scoring and is never
part of a prompt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import yaml

from bridge_island.baseline.platform import BaselineInterface
from bridge_island.codegen import Dictionary
from bridge_island.examples import EXAMPLES, examples_dir, load_world
from bridge_island.unique.subsystem import Need, Output
from bridge_island.yamlutil import load_yaml

# Enumerated values are not part of the interface description, so an interface note says what an
# enumerated element can hold. The note is documentation of the new interface, not of the mapping.
INTERFACE_NOTES: dict[str, tuple[tuple[str, str], ...]] = {
    "medical": (
        (
            "AlarmState.alarm_code",
            "In version 2 this element holds one of the names NONE, OCCLUSION, AIR_IN_LINE, "
            "DOOR_OPEN, LOW_BATTERY. In version 1 the same conditions were numeric codes 0 to 4, "
            "in that order.",
        ),
    ),
}


@dataclass(frozen=True)
class Task:
    example: str
    variant: str  # plain | obfuscated | perturbed
    case: str  # "base", or "p01", "p02", ... for perturbed cases
    v1: BaselineInterface
    v2: BaselineInterface
    needs: Mapping[str, tuple[Need, ...]]
    outputs: Mapping[str, tuple[Output, ...]]
    v1_spec: dict[str, Any]
    truth: dict[str, Any]
    notes: tuple[tuple[str, str], ...] = ()
    world: ModuleType | None = field(default=None, compare=False)  # plain only: allows a system run

    @property
    def id(self) -> str:
        return f"{self.example}/{self.variant}/{self.case}"

    @property
    def dictionary(self) -> Dictionary:
        return Dictionary(self.v2, self.needs, self.outputs)


def spec_text(spec: Mapping[str, Any]) -> str:
    """A mapping spec as YAML text (block style, keys in the order given)."""
    return yaml.safe_dump(dict(spec), sort_keys=False, default_flow_style=False, width=100)


def _load_spec(example: str, version: int) -> dict[str, Any]:
    path = examples_dir() / EXAMPLES[example] / f"mapping_v{version}.yaml"
    spec = load_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError(f"{path.name} is not a mapping spec")
    return spec


def plain_task(example: str) -> Task:
    world = load_world(example)
    dic = world.dictionary(2)
    return Task(
        example=example,
        variant="plain",
        case="base",
        v1=world.interface(1),
        v2=world.interface(2),
        needs=dic.needs,
        outputs=dic.outputs,
        v1_spec=_load_spec(example, 1),
        truth=_load_spec(example, 2),
        notes=INTERFACE_NOTES.get(example, ()),
        world=world,
    )
