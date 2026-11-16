"""Seeded regression: the exact results of every scenario for two virtual patients, and the cohort.

Any change to the patient model, controller, devices, loop, contract or metrics that alters a single
number fails here. If a change is intended, regenerate with ``BRIDGE_ISLAND_REGOLD=1`` and review
the diff. Synthetic, illustrative.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bridge_island.clinical import envelope as Env
from bridge_island.clinical import experiment as X
from bridge_island.clinical import loop as L
from bridge_island.clinical import metrics as M
from bridge_island.clinical import scenarios as S
from bridge_island.clinical.physiology import cohort, therapy_for

GOLD = Path(__file__).parent / "data" / "insulin_golden.json"


def _snapshot() -> dict[str, object]:
    rows, _ = X.run_matrix([p.stem for p in S.shipped()], L.ARCHS, [0, 3], 1, workers=1)
    params, rejected = cohort(1)
    loop = L.build_loop("bridge-contract", L.patients(1)[3], S.by_name("pump_silent"))
    loop.run()
    return {
        "rows": rows,
        "cohort": [vars(p) for p in params[:5]] + [{"rejected": rejected}],
        "therapy": [vars(therapy_for(p)) for p in params[:5]],
        "events": M.critical_events(loop)[:80],
        "envelope": [Env.check(c).as_dict() for c in Env.shipped_changes()],
        "ledger": X.ledger(),
    }


def test_results_match_the_golden_file() -> None:
    now = json.loads(json.dumps(_snapshot(), default=str))
    if os.environ.get("BRIDGE_ISLAND_REGOLD"):
        GOLD.parent.mkdir(exist_ok=True)
        GOLD.write_text(json.dumps(now, indent=1, sort_keys=True), encoding="utf-8")
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    for key in gold:
        assert now[key] == gold[key], key
