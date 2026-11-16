"""Find and load the invented example worlds that live in the repository's ``examples/``."""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

from bridge_island.codegen import load_module

EXAMPLES = {"aviation": "aviation_invented", "medical": "medical_device_invented"}
# The closed-loop example runs for simulated days with its own scenario format, so it is registered
# apart from the two classic examples that the short-run app form and the ai-mapping experiment use.
CLINICAL = {"insulin-loop": "insulin_loop_invented"}
ALL_EXAMPLES = {**EXAMPLES, **CLINICAL}


def examples_dir() -> Path:
    """``$BRIDGE_ISLAND_EXAMPLES``, else ``./examples``, else the checkout next to ``src/``."""
    candidates = [
        Path(p) for p in (os.environ.get("BRIDGE_ISLAND_EXAMPLES"), Path.cwd() / "examples") if p
    ]
    candidates.append(Path(__file__).resolve().parents[2] / "examples")
    for path in candidates:
        if path.is_dir():
            return path
    raise FileNotFoundError(
        "examples/ not found: run from a checkout of the repository or set BRIDGE_ISLAND_EXAMPLES"
    )


def load_world(name: str) -> ModuleType:
    if name not in ALL_EXAMPLES:
        raise KeyError(f"unknown example {name!r}; choose from {', '.join(ALL_EXAMPLES)}")
    return load_module(examples_dir() / ALL_EXAMPLES[name] / "world.py")


def spec_path(world: ModuleType, version: int) -> Path:
    return Path(world.HERE) / f"mapping_v{version}.yaml"


def generated_path(world: ModuleType, version: int) -> Path:
    return Path(world.HERE) / "generated" / f"mapping_v{version}.py"
