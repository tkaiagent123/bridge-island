"""Insulin-loop figures (same seed, same bytes), docs-table refresh, and the remaining CLI paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bridge_island.cli import main
from bridge_island.clinical import docs_tables, views
from bridge_island.clinical import experiment as X
from bridge_island.clinical import figures as F

SCEN = ["nominal", "sensor_swap_units", "bridge_stall"]


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return X.run_matrix(
        SCEN, ("direct", "bridge-basic", "bridge-contract"), [0, 1, 2], 1, workers=1
    )[0]


def _digests(out: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())}


def test_figures_are_byte_identical_for_a_seed(rows: list[dict], tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    for name in ("a", "b"):
        F.closed_loop_figures(tmp_path / name, rows, [], 1)
        F.ledger_figure(tmp_path / name, X.ledger())
        F.traceability_figure(tmp_path / name)
    a, b = _digests(tmp_path / "a"), _digests(tmp_path / "b")
    assert a == b
    assert {f"fig{i}" for i in range(1, 8)} <= {n.split("_")[0] for n in a}
    assert all(n.endswith((".png", ".svg")) for n in a)


def test_figures_are_skipped_without_matplotlib(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(F, "_plt", lambda: None)
    assert list(F.closed_loop_figures(tmp_path, [], [], 1)) == ["fig1_svg"]
    assert F.ledger_figure(tmp_path, []) == {} and F.traceability_figure(tmp_path) == {}


def test_the_figure_patient_is_the_median_one(rows: list[dict]) -> None:
    assert 0 <= F.pick_patient(rows, "sensor_swap_units") <= 2


def test_docs_refresh_rewrites_only_marked_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = tmp_path / "p.md"
    page.write_text(
        "a\n<!-- ggl:cohort:start -->\nold\n<!-- ggl:cohort:end -->\nb\n", encoding="utf-8"
    )
    (tmp_path / "q.md").write_text("no blocks\n", encoding="utf-8")
    monkeypatch.setattr(docs_tables, "DOCS", tmp_path)
    assert docs_tables.refresh({"cohort": "new"}) == [page]
    assert docs_tables.blocks(page.read_text(encoding="utf-8")) == {"cohort": "new"}
    assert docs_tables.refresh({"cohort": "new"}) == []


def test_cohort_view_summarizes_thirty_patients(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict]
) -> None:
    monkeypatch.setattr(
        "bridge_island.clinical.experiment.run_matrix",
        lambda *a, **k: ([r for r in rows if r["scenario"] == "nominal"], {}),
    )
    page = views.cohort_view("nominal", ("direct", "bridge-basic", "bridge-contract"))
    assert "30 virtual patients" in page and "no fault" in page


def test_cli_run_text_and_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(
        "name: s\nexample: insulin-loop\npatients: VP03\n"
        "faults: [{kind: sensor_dropout, at: '02:00', minutes: 30}]\n",
        encoding="utf-8",
    )
    assert main(["run", str(scenario), "--out", str(tmp_path / "o")]) == 0
    out = capsys.readouterr().out
    assert "used unflagged stale glucose" in out and "told in" in out
    assert {p.name for p in (tmp_path / "o").iterdir()} >= {"runs.csv", "summary.csv"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("example: insulin-loop\nwho: x\n", encoding="utf-8")
    assert main(["run", str(bad)]) == 2


def test_cli_verify_everything_and_report_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["envelope", "verify", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["results"]) == 49 and all(r["passed"] for r in data["results"])
    assert main(["demo", "insulin-loop", "--story"]) == 0
    assert "5. " in capsys.readouterr().out


def test_cli_closed_loop_experiment_small(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "experiment",
            "closed-loop",
            "--example",
            "insulin-loop",
            "--patients",
            "1",
            "--workers",
            "1",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert (tmp_path / "closed-loop" / "closed_loop_summary.csv").is_file()
    assert "10 scenarios x 3 architectures x 1 virtual patients" in capsys.readouterr().out
