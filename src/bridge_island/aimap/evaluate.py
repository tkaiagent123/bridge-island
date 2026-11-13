"""Evaluate one reply: parse it, run the contract check, and score what was accepted."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bridge_island.aimap import score
from bridge_island.aimap.parse import entry_ids, parse_response
from bridge_island.aimap.task import Task

PARSE_FAILED = "parse"
CONTRACT_FAILED = "contract"
ACCEPTED = "accepted"


@dataclass(frozen=True)
class Evaluation:
    status: str  # parse | contract | accepted
    error: str  # what to tell the model on a repair round; "" when accepted
    results: list[score.EntryResult] = field(default_factory=list)
    proposal_entries: int = 0  # entries the proposal contains (accepted proposals only)
    review_entries: int = 0  # of those, how many a reviewer reading low and medium would read
    system_differences: tuple[str, ...] = ()  # inputs whose delivered data differ (plain only)


def evaluate(task: Task, reply: str, workdir: Path | None = None) -> Evaluation:
    parsed = parse_response(reply)
    if parsed.spec is None:
        return Evaluation(PARSE_FAILED, parsed.error, score.fail_all(task, score.UNPARSEABLE))
    with tempfile.TemporaryDirectory(prefix="aimap_") as tmp:
        error, mapping_py = score.contract_check(task, parsed.spec, workdir or Path(tmp))
        if mapping_py is None:
            return Evaluation(CONTRACT_FAILED, error, score.fail_all(task, score.REJECTED))
        ids = entry_ids(parsed.spec)
        levels = [parsed.confidence.get(i, (score.NO_CONFIDENCE, ""))[0] for i in ids]
        return Evaluation(
            ACCEPTED,
            "",
            score.score_accepted(task, parsed.spec, parsed.confidence),
            proposal_entries=len(ids),
            review_entries=sum(1 for lv in levels if lv in score.FLAGGED),
            system_differences=tuple(score.system_differences(task, mapping_py)),
        )
