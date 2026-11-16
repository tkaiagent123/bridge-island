"""The requirements traceability matrix (RQ1): load it and render it for the docs and Fig. 7.

An engineering illustration, not a regulatory submission and not a claim of compliance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge_island.clinical import loop as L
from bridge_island.yamlutil import load_yaml

STATUSES = ("demonstrated", "partial", "not addressed", "not addressed (outside the Bridge)")
START, END = "<!-- ggl:traceability:start -->", "<!-- ggl:traceability:end -->"


def path() -> Path:
    return Path(L.world().HERE) / "traceability.yaml"


def load() -> dict[str, Any]:
    data = load_yaml(path().read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def markdown(data: dict[str, Any] | None = None) -> str:
    data = data or load()
    lines = [
        "| Id | Requirement (paraphrase, citation) | Contract field | Bridge behaviour | Tests | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in data["rows"]:
        tests = "<br>".join(f"`{t.split('::')[1]}`" for t in r["tests"]) or "none"
        cite = f"{r['source']} {r['section']}"
        lines.append(
            f"| {r['id']} | {r['requirement']} ({cite}) | {r['contract_field']} | {r['bridge_behaviour']} | {tests} | {r['status']} |"
        )
    return "\n".join(lines)


def counts(data: dict[str, Any] | None = None) -> dict[str, int]:
    data = data or load()
    out = dict.fromkeys(STATUSES, 0)
    for r in data["rows"]:
        out[r["status"]] += 1
    return out
