"""The pipeline: repair rounds, outcomes, cassettes and the files the experiment writes."""

import copy
import csv
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from bridge_island.aimap import controls, run, score
from bridge_island.aimap import providers as P
from bridge_island.aimap.parse import entry_ids
from bridge_island.aimap.task import Task, plain_task
from bridge_island.cli import main

Reply = Callable[[Task, P.Request], str]


class Scripted:
    """A stand-in for a model, for tests only: replies come from a function."""

    provider, model = "scripted", "test-model"

    def __init__(self, reply: Reply, task: Task) -> None:
        self.reply, self.task, self.calls = reply, task, 0

    def complete(self, request: P.Request) -> P.Completion:
        self.calls += 1
        return P.Completion(self.reply(self.task, request), tokens_in=100, tokens_out=50)


def oracle_text(task: Task, level: str = "high", drop_range: bool = False) -> str:
    spec = {k: copy.deepcopy(v) for k, v in task.truth.items() if k != "world"}
    if drop_range:  # accepted by the contract check, behaves differently
        lat = next(e for e in spec["downlinks"] if e["to"] == "PlanningDisplay.nav_lat_deg")
        del lat["transforms"]
    items = [{"entry": e, "confidence": level, "reason": "t"} for e in entry_ids(spec)]
    return yaml.safe_dump({"mapping": spec, "confidence": items}, sort_keys=False)


def test_a_good_first_reply_needs_no_repair() -> None:
    task = plain_task("aviation")
    prov = Scripted(lambda t, r: oracle_text(t), task)
    trial = run.run_model_trial(task, prov, 0, repairs=2)
    assert (trial.rounds, prov.calls) == (1, 1) and trial.tokens_in == 100
    assert set(trial.counts()) <= {score.CORRECT, score.REMOVED_HANDLED}


def test_a_repair_round_is_sent_the_error_and_can_fix_the_reply() -> None:
    task = plain_task("aviation")
    seen: list[str] = []

    def reply(t: Task, r: P.Request) -> str:
        seen.append(r.prompt)
        return "not yaml: [" if r.round == 0 else oracle_text(t)

    trial = run.run_model_trial(task, Scripted(reply, task), 0, repairs=2)
    assert trial.rounds == 2 and trial.evaluation.status == "accepted"
    assert "What was wrong with it" in seen[1] and "not valid YAML" in seen[1]
    assert seen[1].startswith(seen[0])  # the repair prompt is the first prompt plus the feedback
    assert trial.tokens_in == 200  # every round is counted


def test_repairs_run_out() -> None:
    task = plain_task("aviation")
    prov = Scripted(lambda t, r: controls.carry_forward(t), task)
    trial = run.run_model_trial(task, prov, 0, repairs=2)
    assert prov.calls == 3 and trial.evaluation.status == "contract"
    assert set(trial.counts()) == {score.REJECTED}
    no_repairs = Scripted(lambda t, r: controls.carry_forward(t), task)
    assert run.run_model_trial(task, no_repairs, 0, repairs=0).rounds == 1


def test_an_escape_is_counted_only_when_marked_high() -> None:
    task = plain_task("aviation")
    high = run.run_model_trial(
        task, Scripted(lambda t, r: oracle_text(t, "high", True), task), 0, 0
    )
    low = run.run_model_trial(task, Scripted(lambda t, r: oracle_text(t, "low", True), task), 0, 0)
    assert high.counts()[score.WRONG_UNFLAGGED] == 1 and high.counts()[score.WRONG_FLAGGED] == 0
    assert low.counts()[score.WRONG_FLAGGED] == 1 and low.counts()[score.WRONG_UNFLAGGED] == 0
    assert low.evaluation.review_entries == low.evaluation.proposal_entries


def test_the_summary_metrics_and_their_intervals() -> None:
    task = plain_task("aviation")
    good = run.run_control("oracle", task)
    bad = run.run_model_trial(task, Scripted(lambda t, r: oracle_text(t, "high", True), task), 0, 0)
    rows = run.summary_rows([good, good, bad, bad], seed=1)
    by = {(r["provider"], r["metric"]): r for r in rows}
    assert by[("oracle", "correct")]["value"] == 1.0
    assert by[("oracle", "wrong_unflagged")]["value"] == 0.0
    assert by[("scripted", "escape_rate")]["value"] == 1.0  # its only wrong entry was marked high
    assert by[("scripted", "trials_with_an_escape")]["value"] == 1.0
    assert by[("oracle", "escape_rate")]["value"] == ""  # nothing wrong: the ratio is undefined
    assert run.summary_rows([good, good, bad, bad], seed=1) == rows  # seeded
    mixed = run.summary_rows([bad] * 15 + [good] * 15, seed=1)
    row = next(r for r in mixed if r["metric"] == "wrong_unflagged" and r["provider"] == "oracle")
    assert row["ci95_low"] == 0.0 == row["ci95_high"]


def _read(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# synthetic, illustrative"
    return list(csv.DictReader(lines[1:]))


def test_the_command_runs_the_controls_with_no_cassettes(tmp_path: Path) -> None:
    empty = tmp_path / "none"
    code = main(
        [
            "experiment",
            "ai-mapping",
            "--example",
            "aviation",
            "--out",
            str(tmp_path),
            "--cassettes",
            str(empty),
            "--variant",
            "plain,obfuscated",
        ]
    )
    assert code == 0
    out = tmp_path / "ai-mapping"
    summary = _read(out / "ai_mapping_summary.csv")
    assert {r["provider"] for r in summary} == {"oracle", "carry_forward", "heuristic"}
    oracle = next(r for r in summary if r["provider"] == "oracle" and r["metric"] == "correct")
    assert oracle["value"] == "1.0"
    assert len(_read(out / "ai_mapping_trials.csv")) == 6  # three controls, two variants
    entries = _read(out / "ai_mapping_entries.csv")
    assert {"entry", "class", "confidence", "reason"} <= set(entries[0])


def test_live_run_records_and_replay_gives_identical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def scripted(name: str, model: str | None) -> Any:
        # one prompt per task: the stand-in answers every task with its own oracle
        class Any_:
            provider, model = "anthropic", "claude-test"

            def complete(self, request: P.Request) -> P.Completion:
                task = tasks[request.task_id]
                text = oracle_text(task, "high", drop_range=request.trial == 1)
                return P.Completion(text, 10, 5, 0.0)

        return Any_()

    tasks = {t.id: t for t in [plain_task("aviation")]}
    monkeypatch.setattr(run, "live_provider", scripted)
    cassettes = tmp_path / "cassettes"
    live = run.ai_mapping(
        tmp_path / "live",
        1,
        "aviation",
        provider="anthropic",
        model="claude-test",
        trials=2,
        cassettes=cassettes,
    )
    assert list(cassettes.glob("*.jsonl"))
    replay = run.ai_mapping(tmp_path / "replay", 1, "aviation", trials=2, cassettes=cassettes)
    for key in ("trials", "entries", "summary"):
        assert live[key].read_text(encoding="utf-8") == replay[key].read_text(encoding="utf-8")
    rows = _read(replay["trials"])
    model_rows = [r for r in rows if r["provider"] == "anthropic"]
    assert [r["wrong_unflagged"] for r in model_rows] == ["0", "1"]  # trial 1 dropped the range


def test_a_missing_recording_is_skipped_and_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cassettes = tmp_path / "c"
    task = plain_task("aviation")
    rec = P.RecordingProvider(
        Scripted(lambda t, r: oracle_text(t), task), cassettes / "scripted__test-model.jsonl"
    )
    run.run_model_trial(task, rec, 0, 0)  # only trial 0 is recorded
    run.ai_mapping(tmp_path / "o", 1, "aviation", trials=3, cassettes=cassettes)
    assert "2 request(s) had no recorded response" in capsys.readouterr().out
    rows = _read(tmp_path / "o" / "ai_mapping_trials.csv")
    assert Counter(r["provider"] for r in rows)["scripted"] == 1


def test_unknown_names_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown variant"):
        run.ai_mapping(tmp_path, 1, "aviation", variants=["nope"], cassettes=tmp_path)
    with pytest.raises(P.ProviderError, match="unknown provider"):
        run.ai_mapping(tmp_path, 1, "aviation", provider="nope", cassettes=tmp_path)
    with pytest.raises(P.ProviderError, match="--model is required"):
        run.ai_mapping(tmp_path, 1, "aviation", provider="ollama", cassettes=tmp_path)
