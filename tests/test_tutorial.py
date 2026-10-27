"""The tutorial in docs/tutorial.md, executed step by step, so the docs cannot drift."""

import shutil
from pathlib import Path

import pytest

from bridge_island.cli import main
from bridge_island.codegen import CodegenError, dictionary_for_spec, generate, load_module
from bridge_island.examples import examples_dir
from bridge_island.rules import check_rules

NEEDS_OLD = """        "StatusLogger": ("""
NEEDS_NEW = """        "RangeEstimator": (
            n("airspeed_ms", "float64", "m/s", "air", PS, 1, 1.0, 5.0),
            n("bus_voltage_v", "float64", "V", "bus", PS, 1, 1.0, 5.0),
        ),
        "StatusLogger": ("""
PROTOCOLS_OLD = '"StatusLogger": CR}'
PROTOCOLS_NEW = '"StatusLogger": CR, "RangeEstimator": PS}'
MAPPING_OLD = "\nuplinks:"
MAPPING_NEW = """
  # RangeEstimator: a new subsystem, added in the tutorial
  - {from: AirData.airspeed_kt, to: RangeEstimator.airspeed_ms, transforms: [{unit: [kt, m/s]}]}
  - {from: PowerStatus.bus_voltage_v, to: RangeEstimator.bus_voltage_v}

uplinks:"""


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    dest = tmp_path / "aviation_invented"
    shutil.copytree(
        examples_dir() / "aviation_invented", dest, ignore=shutil.ignore_patterns("__pycache__")
    )
    return dest


def step1_declare(dest: Path) -> None:
    world = dest / "world.py"
    text = world.read_text()
    assert NEEDS_OLD in text and PROTOCOLS_OLD in text
    world.write_text(text.replace(NEEDS_OLD, NEEDS_NEW).replace(PROTOCOLS_OLD, PROTOCOLS_NEW))


def step2_map(dest: Path) -> None:
    for version in (1, 2):
        spec = dest / f"mapping_v{version}.yaml"
        text = spec.read_text()
        assert text.count(MAPPING_OLD) == 1
        spec.write_text(text.replace(MAPPING_OLD, MAPPING_NEW))


def test_tutorial_a_missing_mapping_fails_the_build_loudly(copy: Path) -> None:
    step1_declare(copy)  # declared the input but not mapped it yet
    spec = copy / "mapping_v2.yaml"
    with pytest.raises(CodegenError, match=r"RangeEstimator\.airspeed_ms is a declared input"):
        generate(spec, dictionary_for_spec(spec))


def test_tutorial_the_new_subsystem_works_after_three_steps(
    copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    step1_declare(copy)
    step2_map(copy)
    for version in (1, 2):
        spec = copy / f"mapping_v{version}.yaml"
        assert main(["codegen", "--spec", str(spec)]) == 0
    assert "wrote" in capsys.readouterr().out
    world = load_module(copy / "world.py")
    system = world.build(2, seed=1)
    system.run(5.0)
    new = system.subsystems["RangeEstimator"]
    assert new.check() == []
    rec = new.latest("airspeed_ms")
    assert rec is not None and rec.datum.value == pytest.approx(240.0 * 1852 / 3600)
    assert system.network.problems() == []
    for version in (1, 2):
        failed = [r.rule for r in check_rules(world, version) if not r.passed]
        assert failed == []
    other = system.subsystems["PlanningDisplay"]
    assert other.check() == [] and not other.unavailable  # nothing else was touched
