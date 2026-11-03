"""The dashboard view: status rules, headline tiles and the board are right and escaped."""

from typing import Any

import pytest

from bridge_island.dashboard import _status, board, dashboard, tiles
from bridge_island.examples import load_world
from bridge_island.scenario import parse_scenario, run_scenario
from bridge_island.summary import summarize, trace_rows


def sub(**changes: Any) -> dict[str, Any]:
    base = {
        "protocol": "pubsub", "received": 10, "estimated": 0, "mean_age_ms": 5.0,
        "time_avg_age_ms": 20.0, "peak_age_ms": 40.0, "stale_s": 0.0, "stale_input": None,
        "late": 0, "issues": [], "unavailable": {},
    }  # fmt: skip
    return base | changes


@pytest.mark.parametrize(
    ("changes", "level"),
    [
        ({}, "good"),
        ({"late": 2}, "watch"),
        ({"stale_s": 0.4, "stale_input": "x"}, "watch"),
        ({"unavailable": {"x": "removed"}}, "bad"),
        ({"issues": ["bad value"]}, "bad"),
        ({"late": 2, "issues": ["bad value"]}, "bad"),  # a problem outranks a warning
    ],
)
def test_status_levels(changes: dict[str, Any], level: str) -> None:
    assert _status(sub(**changes))[0] == level


@pytest.fixture(scope="module")
def stalled() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sc = parse_scenario(
        {"example": "aviation", "duration_s": 8, "events": [{"at": 2.2, "stall": 1.5}]}
    )
    system = run_scenario(sc, load_world("aviation"))[0]
    return summarize(system), trace_rows(system)


def test_headline_tiles_report_the_run(
    stalled: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    summary, _ = stalled
    html = tiles(summary)
    assert "Worst peak age" in html and "2,005.5 ms" in html and "StatusLogger" in html
    total = sum(s["received"] for s in summary["subsystems"].values())
    assert f"{total:,}" in html
    assert "Bridge events" in html


def test_the_board_shows_every_system_with_a_word_and_an_icon(
    stalled: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    summary, _ = stalled
    html = board(summary)
    for name in summary["subsystems"]:
        assert name in html
    assert "▲ Watch" in html and "✓ OK" in html  # status is never colour alone
    assert html.count('class="f peak"') == len(summary["subsystems"])


def test_the_whole_view_has_no_script_and_escapes_names(
    stalled: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    summary, trace = stalled
    html = dashboard(summary, trace, 8.0, "<li>x</li>", "")
    assert (
        "<script" not in html and "How each system fared" in html and "Show the full table" in html
    )
    hostile = {"subsystems": {"<b>x</b>": sub()}, "bridge": summary["bridge"]}
    assert "<b>x</b>" not in board(hostile) and "&lt;b&gt;x&lt;/b&gt;" in board(hostile)
