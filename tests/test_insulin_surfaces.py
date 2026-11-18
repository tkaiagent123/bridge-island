"""The insulin loop's envelope, experiments, CLI, report, story and app pages (synthetic)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from bridge_island.analysis import change_impact as ci
from bridge_island.cli import main
from bridge_island.clinical import envelope as Env
from bridge_island.clinical import experiment as X
from bridge_island.clinical import story, views
from bridge_island.clinical.figures import architecture_svg
from bridge_island.clinical.report import build_report
from bridge_island.scenario import ScenarioError
from bridge_island.ui import App

CHANGES = Env.changes_dir()
EXPECTED = {
    "01_sensor_swap": True,
    "02_sensor_period": True,
    "03_pump_firmware_v2": True,
    "04_pump_bolus_resolution": True,
    "05_display_os_update": True,
    "06_host_baseline_v2": False,
    "07_new_alarm_field": False,
    "08_field_rename": True,
    "09_sensor_lag_beyond": False,
    "10_glucose_units_outside": False,
}


# ---- envelope ----------------------------------------------------------------------------


@pytest.mark.parametrize("stem, inside", sorted(EXPECTED.items()))
def test_envelope_decision_for_every_shipped_change(stem: str, inside: bool) -> None:
    d = Env.check(Env.load_change(CHANGES / f"{stem}.yaml"))
    assert d.inside is inside
    orgs = [o for o, _ in d.owners]
    if inside:
        assert d.tests and not d.reasons and orgs[0] == Env.INTEGRATOR
        assert "Host Maker Org (invented)" not in orgs  # the host is not involved
    else:
        assert d.reasons and not d.tests and "Host Maker Org (invented)" in orgs


def test_the_conformance_suite_is_generated_from_the_contract() -> None:
    names = [c.name for c in Env.suite()]
    assert len(names) == 49 and len(set(names)) == 49
    assert "item/GlucoseReading.glucose/units" in names and "claim/C6" in names
    assert "timing/GlucoseReading/max_age" in names


@pytest.mark.parametrize("devices", ["default", "sensor_swap", "pump_swap"])
def test_the_conformance_suite_passes_on_every_device_set(devices: str) -> None:
    assert [r for r in Env.verify(devices=devices) if r[1]] == []


@pytest.mark.parametrize(
    "data, match",
    [
        ([], "mapping"),
        ({"side": "x", "device": "SensorA"}, "side"),
        ({"side": "unique", "device": "Toaster"}, "device"),
        ({"side": "unique", "device": "SensorA", "declares": {"colour": 1}}, "declares"),
        ({"side": "unique", "device": "SensorA", "simulate": "x"}, "simulate"),
        ({"side": "unique", "device": "SensorA", "extra": 1}, "unknown"),
    ],
)
def test_bad_change_files_are_refused(data: object, match: str) -> None:
    with pytest.raises(ScenarioError, match=match):
        Env.parse_change(data)


def test_unreadable_change_files_are_refused(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text("a: [", encoding="utf-8")
    with pytest.raises(ScenarioError):
        Env.load_change(tmp_path / "c.yaml")
    with pytest.raises(ScenarioError):
        Env.load_change(tmp_path / "missing.yaml")


# ---- the change ledger ------------------------------------------------------------------------


def test_the_ledger_follows_its_counting_model() -> None:
    rows = {(r["change"], r["architecture"]): r for r in X.ledger()}
    assert len(rows) == 30
    swap = {a: rows[("01_sensor_swap", a)] for a in ("direct", "bridge-basic", "bridge-contract")}
    assert swap["direct"]["host_touched"] and not swap["bridge-contract"]["host_touched"]
    assert (
        swap["bridge-basic"]["organizations"] == 3 and swap["bridge-contract"]["organizations"] == 2
    )
    assert swap["bridge-contract"]["tests_to_rerun"] < swap["direct"]["tests_to_rerun"]
    assert swap["direct"]["inside_envelope"] is None
    host = rows[("06_host_baseline_v2", "bridge-contract")]
    assert host["host_touched"] and host["inside_envelope"] is False
    period = rows[("02_sensor_period", "direct")]
    assert period["components_code_change"] == 1  # timing only: no integration code


def test_ledger_sensitivity_moves_direct_adapters_into_the_devices() -> None:
    rows = {(r["change"], r["architecture"]): r for r in X.ledger(adapters_in_host=False)}
    assert not rows[("01_sensor_swap", "direct")]["host_touched"]
    row = ci.ledger_row(
        "direct", "unique", frozenset({"field_renames"}), "D", "H", "I", (), False, 0, 49
    )
    assert (row.components_code_change, row.organizations, row.host_touched) == (2, 2, True)


# ---- experiments: same seed, same bytes -------------------------------------------------------


def test_closed_loop_csvs_are_byte_identical_for_a_seed(tmp_path: Path) -> None:
    def once(out: Path) -> dict[str, bytes]:
        files = X.closed_loop(
            out, 1, ["stale_burst", "replayed_command"], [0, 5], workers=1, figures=False
        )
        return {k: p.read_bytes() for k, p in files.items()}

    a, b = once(tmp_path / "a"), once(tmp_path / "b")
    assert a == b and a["runs"].startswith(b"# synthetic, illustrative\n")
    assert "events_stale_burst" in a


def test_the_summary_counts_and_medians(tmp_path: Path) -> None:
    rows, _ = X.run_matrix(
        ["nominal", "pump_fw_v2"], ("direct", "bridge-basic"), [0, 3], 1, workers=1
    )
    summary = {(s["scenario"], s["architecture"]): s for s in X.summarize(rows)}
    assert summary[("nominal", "direct")]["detected"] is None
    assert summary[("pump_fw_v2", "direct")]["unit_errors_to_pump_total"] > 100
    assert summary[("pump_fw_v2", "bridge-basic")]["unit_errors_to_pump_total"] == 0


def test_change_ledger_experiment_writes_its_files(tmp_path: Path) -> None:
    files = X.change_ledger(tmp_path, 1, figures=False)
    assert {"csv", "decisions", "sensitivity"} <= set(files)
    assert files["csv"].read_text(encoding="utf-8").startswith("# synthetic, illustrative")


# ---- command line -----------------------------------------------------------------------------


def test_cli_check_rules_shows_all_eight(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check-rules", "--example", "insulin-loop", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [r["rule"] for r in data["results"]] == [f"G{i}" for i in range(1, 9)]


def test_cli_envelope_check_and_verify(capsys: pytest.CaptureFixture[str]) -> None:
    change = str(CHANGES / "01_sensor_swap.yaml")
    assert main(["envelope", "check", change, "--example", "insulin-loop", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["inside_envelope"] and data["tests_to_rerun"]
    assert main(["envelope", "check", change]) == 0
    assert "INSIDE" in capsys.readouterr().out
    out_change = str(CHANGES / "09_sensor_lag_beyond.yaml")
    assert main(["envelope", "check", out_change]) == 0
    assert "OUTSIDE" in capsys.readouterr().out
    assert main(["envelope", "verify", "--change", change]) == 0
    assert "14/14 conformance tests pass" in capsys.readouterr().out
    assert main(["envelope", "verify", "--change", out_change]) == 2  # nothing Bridge-only to run


def test_cli_run_story_report_and_experiment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = tmp_path / "one.yaml"
    scenario.write_text(
        "name: mine\nexample: insulin-loop\npatients: VP02\n"
        "faults: [{kind: bridge_stall, at: '03:00', minutes: 20}]\n",
        encoding="utf-8",
    )
    assert main(["run", str(scenario), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["runs"]) == 3 and data["summary"][0]["patients"] == 1
    assert data["runs"][1]["paths_failed_in_stall"] == 3  # the file's own fault ran
    assert main(["demo", "insulin-loop", "--story", "--json", "--patient", "VP02"]) == 0
    assert len(json.loads(capsys.readouterr().out)["beats"]) == 5
    assert main(["demo", "insulin-loop", "--patient", "VP99"]) == 2
    out = tmp_path / "r.html"
    assert (
        main(
            [
                "report",
                "insulin-loop",
                "--scenario",
                "pump_silent",
                "--no-cohort",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert "Not medical software" in out.read_text(encoding="utf-8")
    assert main(["report", "insulin-loop", "--out", str(out)]) == 2  # --scenario is required
    assert (
        main(["experiment", "change-ledger", "--out", str(tmp_path)]) == 2
    )  # needs --example insulin-loop
    capsys.readouterr()
    assert (
        main(["experiment", "change-ledger", "--example", "insulin-loop", "--out", str(tmp_path)])
        == 0
    )
    assert (tmp_path / "change-ledger" / "change_ledger.csv").is_file()


def test_cli_list_names_the_insulin_loop(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (
        data["clinical"][0]["example"] == "insulin-loop"
        and len(data["clinical"][0]["scenarios"]) == 10
    )


# ---- story, report, views and the app -----------------------------------------------------------


def test_story_every_beat_says_its_counts(capsys: pytest.CaptureFixture[str]) -> None:
    data = story.story("VP01")
    story.render(data)
    text = capsys.readouterr().out
    assert text.count("\n1. ") == 1 and "5. " in text and "synthetic, illustrative" in text
    assert all(any(ch.isdigit() for ch in line) for beat in data["beats"] for line in beat["says"])


def test_report_is_self_contained_and_says_what_it_is() -> None:
    page = build_report("stale_burst", 1, "VP03", cohort=False)
    assert "<script" not in page and 'src="http' not in page and "<link" not in page
    for text in (
        "Not medical software",
        "Limitations",
        "8. Residual",
        "Critical event record",
        "<svg",
    ):
        assert text in page
    assert "synthetic, illustrative" in architecture_svg()


def test_contract_card_shows_eight_sections_and_claim_results() -> None:
    card = views.contract_card(results={"C1": True, "C2": False})
    for title in views.SECTION_TITLES.values():
        assert title in card
    assert (
        card.count("&#10003; pass") == 1 and card.count("&#10007; fail") == 1 and "not run" in card
    )


def test_ledger_view_and_unknown_change() -> None:
    assert "Outside the envelope" in views.ledger_view("07_new_alarm_field")
    assert "Inside the envelope" in views.ledger_view("05_display_os_update")
    with pytest.raises(KeyError):
        views.ledger_view("nope")


def _app() -> tuple[App, dict[str, str]]:
    return App(0), {"Host": "127.0.0.1:0"}


def _post(app: App, host: dict[str, str], path: str, **form: object) -> str:
    body = urlencode({"t": app.token, **form}, doseq=True).encode()
    return app.handle("POST", path, host, body).body.decode()


def test_app_insulin_pages() -> None:
    app, host = _app()
    home = app.handle("GET", "/insulin", host).body.decode()
    assert "Insulin loop" in home and "Not medical software" in home and "aria-current=page" in home
    page = _post(
        app,
        host,
        "/insulin",
        scenario="pump_fw_v2",
        patient="VP04",
        arch=["direct", "bridge-contract"],
    )
    assert "What happened" in page and "<svg" in page and "Event timeline" in page
    assert "unit or encoding errors got through" in page
    card = app.handle("GET", "/insulin/contract", host).body.decode()
    assert card.count("&#10003; pass") == 6
    changes = app.handle("GET", "/insulin/changes", host).body.decode()
    assert "10_glucose_units_outside" in changes
    assert "Outside the envelope" in _post(
        app, host, "/insulin/changes", change="06_host_baseline_v2"
    )
    assert "Page not found" in app.handle("GET", "/insulin/nope", host).body.decode()


@pytest.mark.parametrize(
    "form, message",
    [
        ({"scenario": "../etc", "patient": "VP01", "arch": "direct"}, "Choose a scenario"),
        ({"scenario": "nominal", "patient": "VP01"}, "at least one architecture"),
        ({"scenario": "nominal", "patient": "VP77", "arch": "direct"}, "Choose a patient"),
    ],
)
def test_app_refuses_bad_insulin_forms(form: dict[str, str], message: str) -> None:
    app, host = _app()
    assert message in _post(app, host, "/insulin", **form)


def test_app_refuses_unknown_change_and_page() -> None:
    app, host = _app()
    assert "Choose a change" in _post(app, host, "/insulin/changes", change="x")
    assert "Unknown insulin-loop page" in _post(app, host, "/insulin/other")


def test_cohort_runs_are_desktop_only_in_the_browser() -> None:
    app, host = _app()
    app.browser = True
    assert "desktop only" in app.handle("GET", "/insulin", host).body.decode()
    assert "desktop only" in _post(
        app, host, "/insulin", scenario="nominal", patient="cohort", arch="direct"
    )


def test_app_insulin_tab_has_the_main_simulations_features() -> None:
    app, host = _app()
    for path, text in [
        ("/insulin", "Quick starts"),
        ("/insulin/scenarios", "Run the pasted scenario"),
        ("/insulin/report", "Open the report"),
        ("/insulin/guidelines", "G8"),
        ("/insulin/experiments", "Change ledger"),
        ("/insulin/help", "Fault kinds"),
    ]:
        assert text in app.handle("GET", path, host).body.decode()
    page = _post(
        app,
        host,
        "/insulin",
        scenario="sensor_dropout",
        patient="VP02",
        arch="direct",
        f1_kind="bridge_stall",
        f1_at="lunch+120",
        f1_minutes="20",
        duration_h="12",
    )
    assert "plus_your_faults" in page and "Keep the results" in page and "12 h simulated" in page
    assert "scenario.yaml" in page and "Change something and run again" in page
    assert "8 of 8 pass" in _post(app, host, "/insulin/guidelines")
    assert "Result" in _post(app, host, "/insulin/scenarios", name="pump_silent")
    assert "Choose a scenario" in _post(app, host, "/insulin/scenarios", name="../x")
    assert "clock time runs" in _post(
        app,
        host,
        "/insulin",
        scenario="nominal",
        patient="VP01",
        arch="direct",
        f1_kind="sensor_dropout",
        f1_at="25:99",
    )
    doc = app.handle(
        "POST",
        "/insulin/report",
        host,
        urlencode(
            {"t": app.token, "scenario": "nominal", "patient": "VP01", "mode": "download"}
        ).encode(),
    )
    assert (
        "attachment" in doc.headers["Content-Disposition"] and b"Generic Glycemic Loop" in doc.body
    )
