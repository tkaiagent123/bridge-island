"""The traceability matrix cites only tests that exist and pass, and the docs show it as it is."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bridge_island.clinical import traceability as T

ROOT = Path(__file__).resolve().parent.parent
DATA = T.load()


def test_every_row_is_complete_and_honest() -> None:
    ids = [r["id"] for r in DATA["rows"]]
    assert len(ids) == len(set(ids)) >= 12
    for r in DATA["rows"]:
        assert r["source"] in DATA["sources"] and r["status"] in T.STATUSES
        assert r["requirement"] and r["bridge_behaviour"] and r["contract_field"]
        if r["status"] == "demonstrated":
            assert r["tests"], r["id"]
        if r["status"].startswith("not addressed"):
            assert not r["tests"], r["id"]
    for label in ("clinical performance", "human factors", "training and labeling"):
        row = next(r for r in DATA["rows"] if r["section"] == label)
        assert row["status"] == "not addressed (outside the Bridge)"


def test_every_cited_test_exists() -> None:
    for r in DATA["rows"]:
        for ref in r["tests"]:
            file, _, name = ref.partition("::")
            assert f"def {name}(" in (ROOT / file).read_text(encoding="utf-8"), ref


def test_every_cited_test_passes() -> None:
    refs = sorted({t for r in DATA["rows"] for t in r["tests"]})
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *refs],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-2000:]


def test_the_docs_page_shows_the_matrix_as_it_is() -> None:
    page = (ROOT / "docs" / "insulin-loop-traceability.md").read_text(encoding="utf-8")
    shown = page.split(T.START, 1)[1].split(T.END, 1)[0].strip()
    assert shown == T.markdown(DATA)
    assert "not a regulatory submission" in page and "not a claim of compliance" in page
