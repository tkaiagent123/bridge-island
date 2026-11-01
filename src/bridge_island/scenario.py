"""Scenario files: one YAML file describes one reproducible run.

Example::

    name: stall_and_resume
    description: Stall the Bridge for 1.5 s and watch the consumers.
    example: aviation            # aviation | medical
    version: 2                   # baseline interface version (default: the first)
    seed: 1
    duration_s: 8
    bridge:                      # optional Bridge settings
      time_tag: source           # source | delivery
      rate_policy: cap           # cap | extrapolate
    events:
      - {at: 2.2, stall: 1.5}
      - {at: 1.0, send: {from: PlanningDisplay, message: show_page,
                         values: {page: MAP, priority: 1}}}
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

import bridge_island
from bridge_island.examples import EXAMPLES, generated_path
from bridge_island.system import System

TOP_KEYS = {"name", "description", "example", "version", "seed", "duration_s", "bridge", "events"}
BRIDGE_KEYS = {"time_tag", "rate_policy", "processing_delay_s", "max_extrapolation_s"}


class ScenarioError(Exception):
    """The scenario file is invalid. The message says what to fix."""


@dataclass
class Scenario:
    name: str
    example: str
    duration_s: float
    seed: int = 1
    version: int | None = None
    description: str = ""
    bridge: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


def _number(value: Any, what: str, minimum: float = 0.0, strict: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioError(f"{what} must be a number, got {value!r}")
    if value < minimum or (strict and value == minimum):
        raise ScenarioError(f"{what} must be {'>' if strict else '>='} {minimum}, got {value}")
    return float(value)


def parse_scenario(data: Any, source: str = "scenario") -> Scenario:
    if not isinstance(data, dict):
        raise ScenarioError(f"{source}: expected a mapping at the top level")
    unknown = sorted(set(data) - TOP_KEYS)
    if unknown:
        raise ScenarioError(f"{source}: unknown key(s) {unknown}; allowed: {sorted(TOP_KEYS)}")
    for key in ("example", "duration_s"):
        if key not in data:
            raise ScenarioError(f"{source}: missing required key '{key}'")
    if data["example"] not in EXAMPLES:
        raise ScenarioError(
            f"{source}: example must be one of {sorted(EXAMPLES)}, got {data['example']!r}"
        )
    duration = _number(data["duration_s"], "duration_s", 0.0, strict=True)
    bridge = {} if data.get("bridge") is None else data["bridge"]  # a blank YAML value is None
    if not isinstance(bridge, dict) or set(bridge) - BRIDGE_KEYS:
        raise ScenarioError(
            f"{source}: bridge settings must be a mapping with keys from {sorted(BRIDGE_KEYS)}"
        )
    if bridge.get("time_tag", "source") not in ("source", "delivery"):
        raise ScenarioError(f"{source}: bridge.time_tag must be 'source' or 'delivery'")
    if bridge.get("rate_policy", "cap") not in ("cap", "extrapolate"):
        raise ScenarioError(f"{source}: bridge.rate_policy must be 'cap' or 'extrapolate'")
    for key in ("processing_delay_s", "max_extrapolation_s"):
        if key in bridge:
            _number(bridge[key], f"bridge.{key}")
    events = [] if data.get("events") is None else data["events"]  # a blank YAML value is None
    if not isinstance(events, list):
        raise ScenarioError(f"{source}: events must be a list")
    for i, event in enumerate(events):
        where = f"{source}: events[{i}]"
        if not isinstance(event, dict) or "at" not in event:
            raise ScenarioError(f"{where} needs an 'at' time")
        at = _number(event["at"], f"{where}.at")
        if at > duration:
            raise ScenarioError(f"{where}.at ({at}) is after the end of the run ({duration})")
        kinds = [k for k in event if k != "at"]
        if kinds not in (["stall"], ["send"]):
            raise ScenarioError(f"{where} must have exactly one of 'stall' or 'send', got {kinds}")
        if "stall" in event:
            _number(event["stall"], f"{where}.stall", 0.0, strict=True)
        else:
            send = event["send"]
            if not isinstance(send, dict) or not {"from", "message", "values"} <= set(send):
                raise ScenarioError(f"{where}.send needs 'from', 'message' and 'values'")
            if not isinstance(send["values"], dict):
                raise ScenarioError(f"{where}.send.values must be a mapping")
    version = data.get("version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise ScenarioError(f"{source}: version must be an integer")
    seed = data.get("seed", 1)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScenarioError(f"{source}: seed must be an integer")
    return Scenario(
        name=str(data.get("name", Path(source).stem)),
        example=data["example"],
        duration_s=duration,
        seed=seed,
        version=version,
        description=str(data.get("description", "")),
        bridge=dict(bridge),
        events=[dict(e) for e in events],
    )


def load_scenario(path: Path) -> Scenario:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError(f"cannot read {path}: {exc.strerror}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: not valid YAML: {exc}") from exc
    return parse_scenario(data, str(path))


def run_scenario(scenario: Scenario, world: ModuleType) -> tuple[System, int]:
    """Build the system, schedule the events and run it. Returns the system and the version."""
    version = scenario.version if scenario.version is not None else world.VERSIONS[0]
    if version not in world.VERSIONS:
        raise ScenarioError(
            f"{scenario.name}: the {scenario.example} example has baseline versions {list(world.VERSIONS)}, not {version}"
        )
    system = world.build(version, scenario.seed, **scenario.bridge)
    assert system.bridge is not None
    for i, event in enumerate(scenario.events):
        at = float(event["at"])
        if "stall" in event:
            system.bridge.stall(at, float(event["stall"]))
            continue
        send = event["send"]
        sender = system.subsystems.get(send["from"])
        if sender is None:
            raise ScenarioError(
                f"{scenario.name}: events[{i}].send.from {send['from']!r} is not a subsystem; "
                f"choose from {sorted(system.subsystems)}"
            )
        system.sched.at(
            at,
            lambda s=sender, m=send["message"], v=dict(send["values"]): s.send(m, v),
        )
    system.run(scenario.duration_s)
    return system, version


def manifest(scenario: Scenario, world: ModuleType, version: int) -> dict[str, Any]:
    """Everything needed to reproduce the run. Deliberately free of wall-clock times."""
    mapping = generated_path(world, version).read_text(encoding="utf-8").splitlines()
    sha = next(
        (line.split(": ", 1)[1] for line in mapping if line.startswith("# Spec SHA-256")), ""
    )
    return {
        "bridge_island_version": bridge_island.__version__,
        "python": ".".join(map(str, sys.version_info[:3])),
        "scenario": {
            "name": scenario.name,
            "example": scenario.example,
            "baseline_version": version,
            "seed": scenario.seed,
            "duration_s": scenario.duration_s,
            "bridge": scenario.bridge,
            "events": scenario.events,
        },
        "mapping_spec_sha256": sha,
        "note": "synthetic, illustrative",
    }


def fault_messages(world: ModuleType, version: int) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Messages a faulty subsystem could send: key -> (plain description, message name, values)."""
    cfg = world.CONTAINMENT
    accepted = world.interface(version).accepted[0]
    bad = {e.name: 0 for e in accepted.elements} | {accepted.elements[0].name: 99}
    return {
        "valid": ("A valid request", cfg["message"], dict(cfg["good"])),
        "out_of_range": ("A value that is out of range", cfg["message"], dict(cfg["out_of_range"])),
        "bad_enum": ("A name that does not exist", cfg["message"], dict(cfg["bad_enum"])),
        "malformed": ("A message with a missing field", cfg["message"], dict(cfg["malformed"])),
        "unauthorized": ("A message type nobody authorized", cfg["unauthorized"], {}),
        "baseline_format": (
            "A baseline-format message with a bad value",
            accepted.name,
            bad,
        ),
    }
