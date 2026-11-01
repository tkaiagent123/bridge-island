"""Scenario files, describe, diff, report, list, JSON output, colour and error handling."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar

import pytest

from bridge_island.cli import main
from bridge_island.examples import examples_dir, load_world
from bridge_island.report import build_report
from bridge_island.scenario import (
    ScenarioError,
    load_scenario,
    manifest,
    parse_scenario,
    run_scenario,
)
from bridge_island.summary import render_text, summarize, trace_rows
from bridge_island.term import color_enabled, paint

SCENARIOS = sorted((examples_dir() / "scenarios").glob("*.yaml"))


def run_file(name: str) -> tuple[Any, dict[str, Any]]:
    sc = load_scenario(examples_dir() / "scenarios" / f"{name}.yaml")
    system, _ = run_scenario(sc, load_world(sc.example))
    return system, summarize(system)


# --- scenarios ----------------------------------------------------------------------------


def test_every_shipped_scenario_loads_and_runs() -> None:
    assert len(SCENARIOS) >= 5
    for path in SCENARIOS:
        sc = load_scenario(path)
        system, version = run_scenario(sc, load_world(sc.example))
        assert system.sched.now == sc.duration_s and version in load_world(sc.example).VERSIONS
        assert system.network.problems() == []


def test_stall_scenario_is_detected_and_naive_stamping_hides_it() -> None:
    _, honest = run_file("stall_and_resume")
    _, naive = run_file("naive_time_stamping")
    kinds = honest["bridge"]["event_counts"]
    assert kinds["health.bridge_stall"] == 1 and "bridge.resume" in kinds
    assert honest["subsystems"]["VoiceRelay"]["late"] >= 1
    assert naive["subsystems"]["VoiceRelay"]["late"] == 0
    assert honest["subsystems"]["StatusLogger"]["issues"] == []  # recovered after the stall


def test_removal_and_fault_and_extrapolation_scenarios() -> None:
    _, removal = run_file("baseline_v2_removal")
    assert "baro_alt_m" in removal["subsystems"]["CueingProcessor"]["unavailable"]
    _, fault = run_file("fault_injection")
    assert fault["platform"]["accepted"] == 1 and fault["platform"]["rejected"] == []
    assert fault["bridge"]["blocked_by_cyber_monitor"] == 2
    assert fault["bridge"]["rejected_by_translator"] == 1
    _, ext = run_file("extrapolate_rate_policy")
    assert ext["bridge"]["estimates_delivered"] > 0
    assert ext["subsystems"]["CueingProcessor"]["estimated"] > 0


def test_scenarios_and_manifests_are_reproducible() -> None:
    sc = load_scenario(examples_dir() / "scenarios" / "stall_and_resume.yaml")
    world = load_world(sc.example)
    a, va = run_scenario(sc, world)
    b, _ = run_scenario(sc, world)
    assert summarize(a) == summarize(b) and trace_rows(a) == trace_rows(b)
    man = manifest(sc, world, va)
    assert man == manifest(sc, world, va) and len(man["mapping_spec_sha256"]) == 64
    assert not re.search(r"20\d\d-\d\d-\d\d", json.dumps(man))  # no wall-clock timestamps


@pytest.mark.parametrize(
    "data, message",
    [
        ("just text", "mapping at the top level"),
        ({"example": "aviation"}, "missing required key 'duration_s'"),
        ({"example": "submarine", "duration_s": 1}, "example must be one of"),
        ({"example": "aviation", "duration_s": 0}, "duration_s must be > 0"),
        ({"example": "aviation", "duration_s": "x"}, "must be a number"),
        ({"example": "aviation", "duration_s": 1, "colour": 1}, "unknown key"),
        ({"example": "aviation", "duration_s": 1, "bridge": {"speed": 1}}, "bridge settings"),
        ({"example": "aviation", "duration_s": 1, "bridge": {"time_tag": "x"}}, "time_tag"),
        ({"example": "aviation", "duration_s": 1, "bridge": {"rate_policy": "x"}}, "rate_policy"),
        ({"example": "aviation", "duration_s": 1, "events": {}}, "events must be a list"),
        ({"example": "aviation", "duration_s": 1, "events": [{"stall": 1}]}, "needs an 'at'"),
        (
            {"example": "aviation", "duration_s": 1, "events": [{"at": 5, "stall": 1}]},
            "after the end",
        ),
        (
            {"example": "aviation", "duration_s": 1, "events": [{"at": 0, "stall": 1, "send": {}}]},
            "exactly one",
        ),
        (
            {"example": "aviation", "duration_s": 1, "events": [{"at": 0, "send": {"from": "X"}}]},
            "needs 'from'",
        ),
        (
            {
                "example": "aviation",
                "duration_s": 1,
                "events": [{"at": 0, "send": {"from": "X", "message": "m", "values": 1}}],
            },
            "must be a mapping",
        ),
        ({"example": "aviation", "duration_s": 1, "version": "2"}, "version must be an integer"),
        ({"example": "aviation", "duration_s": 1, "seed": 1.5}, "seed must be an integer"),
    ],
)
def test_bad_scenarios_say_what_to_fix(data: Any, message: str) -> None:
    with pytest.raises(ScenarioError, match=message):
        parse_scenario(data, "t.yaml")


def test_scenario_errors_that_need_the_world_or_the_file(tmp_path: Path) -> None:
    sc = parse_scenario({"example": "aviation", "duration_s": 1, "version": 9})
    with pytest.raises(ScenarioError, match="baseline versions"):
        run_scenario(sc, load_world("aviation"))
    sc = parse_scenario(
        {
            "example": "aviation",
            "duration_s": 1,
            "events": [{"at": 0, "send": {"from": "Nobody", "message": "m", "values": {}}}],
        }
    )
    with pytest.raises(ScenarioError, match="is not a subsystem"):
        run_scenario(sc, load_world("aviation"))
    with pytest.raises(ScenarioError, match="cannot read"):
        load_scenario(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("a: [unclosed")
    with pytest.raises(ScenarioError, match="not valid YAML"):
        load_scenario(broken)


# --- commands -----------------------------------------------------------------------------


def out_json(capsys: pytest.CaptureFixture[str]) -> Any:
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err  # stdout must be pure JSON


def test_list_text_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list"]) == 0
    text = capsys.readouterr().out
    assert "aviation" in text and "medical" in text and "stall_and_resume.yaml" in text
    assert "Next: bridge-island demo" in text
    assert main(["list", "--json"]) == 0
    data, _ = out_json(capsys)
    assert {e["example"] for e in data["examples"]} == {"aviation", "medical"}
    assert len(data["scenarios"]) >= 5


def test_describe_in_every_format(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["describe", "aviation", "--version", "2"]) == 0
    text = capsys.readouterr().out
    assert (
        "REMOVED" in text and "unit m->ft" in text and "10 identity, 7 converted, 1 removed" in text
    )
    assert main(["describe", "medical", "--format", "markdown"]) == 0
    assert "| From | To | Path |" in capsys.readouterr().out
    assert main(["describe", "aviation", "--format", "mermaid"]) == 0
    mermaid = capsys.readouterr().out
    assert (
        mermaid.startswith("flowchart LR") and "UID -->" in mermaid and "subgraph bridge" in mermaid
    )
    assert main(["describe", "aviation", "--format", "json", "--version", "1"]) == 0
    data, _ = out_json(capsys)
    assert data["counts"] == {
        "published_elements": 13,
        "subsystems": 4,
        "paths": 18,
        "identity": 8,
        "converted": 10,
        "removed": 0,
    }


def test_diff_measures_the_real_mapping_change(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "aviation"]) == 0
    text = capsys.readouterr().out
    assert (
        "6 interface changes" in text and "CueingProcessor must handle the removed element" in text
    )
    assert "comments and blank lines not counted" in text and "use --full" in text
    assert main(["diff", "aviation", "--format", "json"]) == 0
    data, _ = out_json(capsys)
    assert data["mapping_spec"]["added"] == 11 and data["mapping_spec"]["removed"] == 8
    assert data["mapping_spec"]["added_including_comments"] > data["mapping_spec"]["added"]
    assert data["modelled_impact"]["bridge"]["subsystems_code_change"] == 1
    assert main(["diff", "medical", "--full"]) == 0
    assert "use --full" not in capsys.readouterr().out


def test_run_json_trace_and_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scenario = str(examples_dir() / "scenarios" / "fault_injection.yaml")
    out_dir, trace = tmp_path / "out", tmp_path / "t" / "trace.csv"
    assert main(["run", scenario, "--json", "--out", str(out_dir), "--trace", str(trace)]) == 0
    data, err = out_json(capsys)
    assert data["summary"]["platform"]["accepted"] == 1 and "manifest" in data
    assert "wrote" in err  # messages go to stderr, never into the JSON on stdout
    assert json.loads((out_dir / "summary.json").read_text()) == data["summary"]
    assert json.loads((out_dir / "manifest.json").read_text()) == data["manifest"]
    for path in (trace, out_dir / "trace.csv"):
        lines = path.read_text().splitlines()
        assert lines[0] == "# synthetic, illustrative" and lines[1].startswith("subsystem,input")
        assert len(lines) > 100


def test_run_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", str(examples_dir() / "scenarios" / "stall_and_resume.yaml")]) == 0
    out = capsys.readouterr().out
    assert "scenario stall_and_resume" in out and "health.bridge_stall" in out and "late" in out
    assert render_text(summarize(run_file("stall_and_resume")[0])).count("\n") > 5


def test_demo_json_is_pure_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo", "medical", "--json", "--rate-policy", "extrapolate"]) == 0
    data, _ = out_json(capsys)
    assert data["rate_policy"] == "extrapolate" and [
        v["baseline_version"] for v in data["versions"]
    ] == [1, 2]
    assert data["change"]["from_version"] == 1


def test_check_rules_json_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check-rules", "--json", "--version", "2"]) == 0
    data, _ = out_json(capsys)
    assert data["passed"] and [r["rule"] for r in data["results"]] == [
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "G6",
    ]


# --- errors and exit codes ----------------------------------------------------------------


def test_errors_are_rewritten_for_humans_with_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("example: aviation\nduration_s: -3\n")
    assert main(["run", str(bad)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "duration_s must be > 0" in err and "hint:" in err
    assert main(["run", str(tmp_path / "nope.yaml")]) == 2
    assert "cannot read" in capsys.readouterr().err
    assert main(["describe", "aviation", "--version", "7"]) == 2
    assert "has baseline versions [1, 2], not 7" in capsys.readouterr().err
    assert main(["diff", "aviation", "--from", "2", "--to", "2"]) == 2
    assert "nothing to compare" in capsys.readouterr().err
    assert main(["report", "aviation", "--out", str(tmp_path / "r.html"), "--stall", "soon"]) == 2
    assert "AT,DURATION" in capsys.readouterr().err


def test_unknown_command_or_example_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["describe", "submarine"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_help_leads_with_examples(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert (
        "examples:" in out and "bridge-island run examples/scenarios" in out and "synthetic" in out
    )


# --- colour -------------------------------------------------------------------------------


class Tty:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def test_colour_rules() -> None:
    assert color_enabled(Tty(True), env={})  # type: ignore[arg-type]
    assert not color_enabled(Tty(False), env={})  # type: ignore[arg-type]
    assert not color_enabled(Tty(True), no_color_flag=True, env={})  # type: ignore[arg-type]
    assert not color_enabled(Tty(True), env={"NO_COLOR": "1"})  # type: ignore[arg-type]
    assert not color_enabled(Tty(True), env={"TERM": "dumb"})  # type: ignore[arg-type]
    assert color_enabled(Tty(False), env={"FORCE_COLOR": "1"})  # type: ignore[arg-type]
    assert not color_enabled(object(), env={})  # type: ignore[arg-type]
    assert (
        paint("PASS", "pass", True) == "\x1b[32mPASS\x1b[0m"
        and paint("PASS", "pass", False) == "PASS"
    )


def test_output_has_no_escape_codes_when_not_a_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check-rules"]) == 0
    assert "\x1b" not in capsys.readouterr().out


# --- the HTML report ----------------------------------------------------------------------


class Audit(HTMLParser):
    VOID: ClassVar[set[str]] = {
        "meta",
        "link",
        "br",
        "hr",
        "img",
        "input",
        "path",
        "line",
        "rect",
        "circle",
        "polygon",
        "marker",
    }

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.refs: list[str] = []
        self.svgs = 0
        self.titles_in_svg = 0
        self.external: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"] or "")
        for ref in (a.get("aria-labelledby") or "").split():
            self.refs.append(ref)
        for key in ("src", "href"):
            if a.get(key) and not (a[key] or "").startswith("#"):
                self.external.append(a[key] or "")
        self.svgs += tag == "svg"
        self.scripts += tag == "script"
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> with stack {self.stack[-3:]}")
        else:
            self.stack.pop()


@pytest.fixture(scope="module")
def report_html() -> str:
    return build_report(load_world("aviation"), seed=1, duration_s=6.0, stall=(2.2, 1.5))


def test_report_is_well_formed_offline_and_script_free(report_html: str) -> None:
    audit = Audit()
    audit.feed(report_html)
    assert audit.errors == [] and audit.stack == []
    assert report_html.startswith("<!doctype html>") and '<html lang="en">' in report_html
    assert audit.scripts == 0 and audit.external == []
    assert "http://" not in report_html and "https://" not in report_html
    assert "synthetic, illustrative" in report_html


def test_report_charts_are_accessible(report_html: str) -> None:
    audit = Audit()
    audit.feed(report_html)
    assert audit.svgs >= 9  # architecture + 4 subsystems x 2 versions
    assert report_html.count('role="img"') == audit.svgs
    assert set(audit.refs) <= audit.ids  # every aria-labelledby points at a real element
    assert len(audit.refs) == 2 * audit.svgs  # a title and a description for each
    assert "Hollow markers are estimates" not in report_html  # none under the default policy
    assert "prefers-color-scheme:dark" in report_html and 'class="skip"' in report_html
    assert report_html.count("<caption>") >= 6 and 'scope="col"' in report_html
    assert (
        len({m for m in re.findall(r"<(circle|rect|polygon)", report_html)}) == 3
    )  # shapes differ


def test_report_content_matches_the_simulation(report_html: str) -> None:
    for text in (
        "Baseline interface v1",
        "Baseline interface v2",
        "G1",
        "G6",
        "Bridge stalled at 2.2 s",
    ):
        assert text in report_html
    assert (
        "bridge_stall" in report_html and "REMOVED" not in report_html
    )  # kind column says removed
    assert "removed: element removed from baseline interface v2" in report_html
    assert "+11 / &minus;8 lines" in report_html
    assert report_html == build_report(
        load_world("aviation"), 1, "cap", 6.0, (2.2, 1.5)
    )  # deterministic


def test_report_marks_estimates_and_escapes_text() -> None:
    page = build_report(load_world("medical"), seed=2, rate_policy="extrapolate", duration_s=4.0)
    assert "Hollow markers are estimates" in page and "rate policy <code>extrapolate</code>" in page
    assert "<script" not in page


def test_report_command_writes_one_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "sub" / "r.html"
    assert (
        main(["report", "medical", "--out", str(out), "--duration", "3", "--stall", "1,0.5"]) == 0
    )
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "self-contained" in capsys.readouterr().out
