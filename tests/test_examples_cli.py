import csv
from pathlib import Path
from types import ModuleType

import pytest

from bridge_island import experiments
from bridge_island.cli import main
from bridge_island.demo import run_demo
from bridge_island.examples import EXAMPLES, examples_dir, load_world
from bridge_island.rules import RULES, check_rules


@pytest.fixture(scope="module", params=sorted(EXAMPLES))
def example(request: pytest.FixtureRequest) -> ModuleType:
    return load_world(request.param)


def test_every_guideline_rule_passes_on_every_example_and_version(example: ModuleType) -> None:
    for version in example.VERSIONS:
        results = check_rules(example, version)
        assert [r.rule for r in results] == [r for r, _, _ in RULES]
        failed = [f"{r.rule}: {r.detail}" for r in results if not r.passed]
        assert not failed, failed


def test_v2_reports_the_removed_element_in_every_example(example: ModuleType) -> None:
    system = example.build(2, seed=1)
    system.run(3.0)
    assert system.bridge is not None
    assert len(system.bridge.log.of_kind("bridge.unavailable")) == 1
    flagged = [s for s in system.subsystems.values() if s.unavailable]
    assert len(flagged) == 1 and "removed" in next(iter(flagged[0].unavailable.values()))
    healthy = [s for s in system.subsystems.values() if s is not flagged[0]]
    assert all(s.check() == [] for s in healthy)
    assert system.network.problems() == [] and system.platform.rejected == []


def test_v1_adapters_on_a_v2_baseline_break_p2p_but_not_the_bridge(example: ModuleType) -> None:
    p2p = example.build_p2p(2, seed=1, adapters_from=1)
    p2p.run(3.0)
    assert p2p.adapters is not None
    assert any(a.errors for a in p2p.adapters.values())  # lookup errors from the renamed field
    assert not any(s.unavailable for s in p2p.subsystems.values())  # nothing says what is gone


def test_medical_v2_conversions_are_correct() -> None:
    med = load_world("medical")
    system = med.build(2, seed=1)
    system.run(4.0)
    pump = med.sources(2)["PumpState"]
    rec = system.subsystems["DoseAdvisor"].latest("pressure_psi")
    assert rec is not None
    mmhg = pump(rec.datum.source_time)["line_pressure"]
    assert rec.datum.value == pytest.approx(mmhg * 0.133322387415 / 6.894757293168361)
    code = system.subsystems["AlarmRelay"].latest("alarm_code")
    assert code is not None and code.datum.value == 0  # "NONE" became the numeric code
    mode = system.subsystems["BedsideDisplay"].latest("mode_name")
    assert mode is not None and mode.datum.value == "RUN"


def test_demo_prints_both_versions_and_the_comparison(
    example: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    lines: list[str] = []
    run_demo(example, seed=1, out=lines.append)
    text = "\n".join(lines)
    assert "synthetic, illustrative" in text
    assert "baseline interface v1" in text and "baseline interface v2" in text
    assert "REPORTED to the subsystem, not silent" in text
    assert "tests failing before the fix" in text
    assert capsys.readouterr().out == ""


def test_cli_demo_and_check_rules(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo", "aviation", "--seed", "2"]) == 0
    assert "bridge-island demo: aviation" in capsys.readouterr().out
    assert main(["check-rules", "--example", "medical"]) == 0
    assert "6/6 rules pass on the medical example" in capsys.readouterr().out


def test_cli_codegen_writes_a_file_and_reports_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = examples_dir() / "aviation_invented" / "mapping_v1.yaml"
    out = tmp_path / "m.py"
    assert main(["codegen", "--spec", str(spec), "--out", str(out)]) == 0
    assert out.read_text().startswith("# GENERATED FILE")
    bad = tmp_path / "mapping_v1.yaml"
    bad.write_text(spec.read_text().replace("[ft, m]", "[ft, km]", 1))
    (tmp_path / "world.py").write_text((spec.parent / "world.py").read_text())
    assert main(["codegen", "--spec", str(bad), "--out", str(tmp_path / "x.py")]) == 1
    assert "codegen failed" in capsys.readouterr().err


def test_load_world_rejects_unknown_names() -> None:
    with pytest.raises(KeyError, match="unknown example"):
        load_world("submarine")


def test_examples_dir_honours_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BRIDGE_ISLAND_EXAMPLES", str(tmp_path))
    assert examples_dir() == tmp_path


@pytest.mark.parametrize("name", ["topology", "change-impact", "timing", "containment"])
def test_experiments_write_labelled_csv(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["experiment", name, "--seed", "1", "--out", str(tmp_path), "--trials", "50"])
    assert code == 0
    csvs = list((tmp_path / name).glob("*.csv"))
    assert csvs
    for path in csvs:
        assert path.read_text().splitlines()[0] == "# synthetic, illustrative"
    assert "synthetic, illustrative" in capsys.readouterr().out


def test_experiments_are_reproducible_for_a_seed(tmp_path: Path) -> None:
    world = load_world("aviation")
    a, b = tmp_path / "a", tmp_path / "b"
    for out, seed in ((a, 3), (b, 3)):
        experiments.run("change-impact", world, seed, out, trials=40)
        experiments.run("topology", world, seed, out)
    for name in ("change-impact/change_impact_monte_carlo.csv", "topology/topology_sweep.csv"):
        assert (a / name).read_bytes() == (b / name).read_bytes()
    c = tmp_path / "c"
    experiments.run("topology", world, 4, c)
    assert (a / "topology/topology_sweep.csv").read_bytes() != (
        c / "topology/topology_sweep.csv"
    ).read_bytes()


def read_rows(path: Path) -> list[dict[str, str]]:
    lines = path.read_text().splitlines()[1:]  # skip the "# synthetic, illustrative" label
    return list(csv.DictReader(lines))


def test_timing_experiment_shows_the_g3_failure(tmp_path: Path) -> None:
    for name in ("aviation", "medical"):
        experiments.run("timing", load_world(name), 1, tmp_path / name)
        source, delivery = read_rows(tmp_path / name / "timing" / "timing_summary.csv")
        assert float(source["max_age_s_of_slow_input"]) > 0.5
        assert int(source["slow_input_flagged_late"]) >= 1
        assert float(delivery["max_age_s_of_slow_input"]) < 0.01
        assert int(delivery["slow_input_flagged_late"]) == 0
        assert (
            "bridge_stall" in source["health_events"] and "discontinuity" in source["health_events"]
        )


def test_containment_experiment_keeps_the_baseline_clean_on_the_bridge(tmp_path: Path) -> None:
    experiments.run("containment", load_world("aviation"), 1, tmp_path)
    rows = read_rows(tmp_path / "containment" / "containment.csv")
    bridge = [r for r in rows if r["architecture"] == "bridge"]
    p2p = [r for r in rows if r["architecture"] == "p2p"]
    assert all(r["baseline_rejected"] == "0" for r in bridge)
    assert sum(int(r["baseline_rejected"]) for r in p2p) == 2  # faults only the baseline can catch
    assert sum(int(r["caught_by_subsystem_adapter"]) for r in p2p) == 3  # caught by subsystem code
    assert bridge[0]["baseline_accepted"] == "1"  # the valid message still gets through


def test_no_color_is_accepted_before_or_after_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--no-color", "list"]) == 0
    before = capsys.readouterr().out
    assert main(["list", "--no-color"]) == 0
    assert capsys.readouterr().out == before
