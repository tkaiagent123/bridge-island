"""Insulin-loop scenario files (``examples/scenarios/insulin_loop/*.yaml``).

Example::

    name: sensor_dropout
    example: insulin-loop
    description: No glucose readings for 45 minutes, starting 15 minutes after lunch.
    duration_h: 24
    seed: 1
    patients: all            # all (VP01..VP30) or one id such as VP01
    architectures: [direct, bridge-basic, bridge-contract]
    faults:
      - {kind: sensor_dropout, at: lunch+15, minutes: 45}

``at`` is a clock time ("08:00") or a meal plus minutes ("lunch+15", "lunch-30"). Synthetic,
illustrative.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from bridge_island.clinical.loop import ARCHS, FAULT_KINDS, MEALS, Fault, LoopScenario
from bridge_island.examples import examples_dir
from bridge_island.scenario import ScenarioError
from bridge_island.yamlutil import YamlError, load_yaml

TOP = {
    "name",
    "example",
    "description",
    "duration_h",
    "seed",
    "patients",
    "architectures",
    "faults",
}
FAULT_KEYS = {"kind", "at", "minutes"}
OPTIONS = {"release_every_min", "readings", "value", "drop", "meal", "fraction", "delay_min"}
MAX_HOURS = 48.0


def scenario_dir() -> Path:
    return examples_dir() / "scenarios" / "insulin_loop"


def shipped() -> list[Path]:
    try:
        return sorted(scenario_dir().glob("*.yaml"))
    except FileNotFoundError:
        return []


def _fault(raw: Any, where: str) -> Fault:
    if not isinstance(raw, dict) or "kind" not in raw or "at" not in raw:
        raise ScenarioError(f"{where} needs 'kind' and 'at'")
    unknown = sorted(set(raw) - FAULT_KEYS - OPTIONS)
    if unknown:
        raise ScenarioError(f"{where}: unknown key(s) {unknown}")
    if raw["kind"] not in FAULT_KINDS:
        raise ScenarioError(f"{where}.kind must be one of {list(FAULT_KINDS)}")
    minutes = raw.get("minutes", 0)
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, (int, float))
        or not 0 <= minutes <= 24 * 60
    ):
        raise ScenarioError(f"{where}.minutes must be a number from 0 to 1440")
    for key in ("release_every_min", "readings", "value", "drop", "fraction", "delay_min"):
        v = raw.get(key)
        if v is not None and (
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        ):
            raise ScenarioError(f"{where}.{key} must be a number >= 0")
    if raw.get("meal") is not None and raw["meal"] not in {m[0] for m in MEALS}:
        raise ScenarioError(f"{where}.meal must be one of {[m[0] for m in MEALS]}")
    fault = Fault(
        str(raw["kind"]),
        str(raw["at"]),
        float(minutes),
        tuple(sorted((k, raw[k]) for k in OPTIONS if k in raw)),
    )
    try:
        fault.resolve(tuple())  # a clock time resolves at once; a meal time is checked below
    except ValueError:
        if not any(str(raw["at"]).startswith(m[0]) for m in MEALS):
            raise ScenarioError(
                f"{where}.at: use HH:MM or <meal>+<minutes>, got {raw['at']!r}"
            ) from None
    return fault


def parse(data: Any, source: str = "scenario") -> tuple[LoopScenario, str, tuple[str, ...]]:
    """The scenario, which patients ("all" or an id) and which architectures."""
    if not isinstance(data, dict):
        raise ScenarioError(f"{source}: expected a mapping at the top level")
    unknown = sorted(set(data) - TOP, key=str)
    if unknown:
        raise ScenarioError(f"{source}: unknown key(s) {unknown}; allowed: {sorted(TOP)}")
    if data.get("example") != "insulin-loop":
        raise ScenarioError(f"{source}: example must be insulin-loop")
    hours = data.get("duration_h", 24)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= MAX_HOURS:
        raise ScenarioError(
            f"{source}: duration_h must be a number above 0 and at most {MAX_HOURS:g}"
        )
    seed = data.get("seed", 1)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScenarioError(f"{source}: seed must be an integer")
    patients = str(data.get("patients", "all"))
    if patients != "all" and not (
        len(patients) == 4
        and patients.startswith("VP")
        and patients[2:].isdigit()
        and 1 <= int(patients[2:]) <= 30
    ):
        raise ScenarioError(f"{source}: patients must be 'all' or VP01..VP30")
    archs = data.get("architectures", list(ARCHS))
    if not isinstance(archs, list) or not archs or set(archs) - set(ARCHS):
        raise ScenarioError(f"{source}: architectures must be a list from {list(ARCHS)}")
    faults_raw = data.get("faults")
    faults_raw = [] if faults_raw is None else faults_raw
    if not isinstance(faults_raw, list):
        raise ScenarioError(f"{source}: faults must be a list")
    faults = tuple(_fault(f, f"{source}: faults[{i}]") for i, f in enumerate(faults_raw))
    kinds = [f.kind for f in faults]
    if "sensor_swap" in kinds and "pump_swap" in kinds:
        raise ScenarioError(f"{source}: one device swap per scenario")
    scenario = LoopScenario(
        str(data.get("name", Path(source).stem)),
        str(data.get("description", "")),
        float(hours) * 3600.0,
        seed,
        faults,
    )
    return scenario, patients, tuple(a for a in ARCHS if a in archs)


def load(path: Path) -> tuple[LoopScenario, str, tuple[str, ...]]:
    try:
        data = load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ScenarioError(f"cannot read {path}: {exc}") from exc
    except YamlError as exc:
        raise ScenarioError(f"{path}: not valid YAML: {exc}") from exc
    return parse(data, str(path))


def by_name(name: str) -> LoopScenario:
    path = scenario_dir() / f"{name}.yaml"
    if not path.is_file():
        raise ScenarioError(
            f"no insulin-loop scenario {name!r}; choose from {[p.stem for p in shipped()]}"
        )
    return load(path)[0]
