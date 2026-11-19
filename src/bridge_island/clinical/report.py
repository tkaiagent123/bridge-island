"""``bridge-island report insulin-loop --scenario NAME``: one self-contained, offline HTML file.

No scripts and no external resources. It holds the architecture figure, one patient's glucose and
insulin charts, the cohort table, the critical event record, the contract card and the limitations.
Synthetic, illustrative; not medical software.
"""

from __future__ import annotations

import html

import bridge_island
from bridge_island.clinical import loop as L
from bridge_island.clinical import metrics as M
from bridge_island.clinical import scenarios as S
from bridge_island.clinical import svg, views
from bridge_island.clinical.figures import architecture_svg
from bridge_island.report import CSS

E = html.escape
LIMITATIONS = (
    "The patient model is invented: a Bergman-structure model with a subcutaneous delay and invented "
    "parameter ranges. It is not validated against any population and says nothing about real people.",
    "The controller is invented and deliberately simple. It was tuned once, on the healthy-data day, so "
    "that no virtual patient goes below 54 mg/dL; the comparisons then isolate interface effects.",
    "Direct adapters have the same range and type checks as the Bridge's mapping, but no age, command-id or "
    "confirmation checks: those are contract features. A direct integrator who wrote them would close much "
    "of the gap shown here.",
    "The counting model of the change ledger is an assumption about where adapters live and who signs, not "
    "a measurement. The sensitivity table shows what changes if direct adapters live in the devices.",
    "There is one implementation of each architecture, written by the same author, on simplified bus "
    "models with no clock drift.",
    "The Bridge is a single point of failure. A stall stops every path and stalls the contract with it.",
)


def build_report(
    scenario_name: str,
    seed: int = 1,
    patient: str = "VP01",
    cohort: bool = True,
    workers: int | None = None,
) -> str:
    scenario = S.by_name(scenario_name)
    index = int(patient[2:]) - 1
    contract_loop = L.build_loop("bridge-contract", L.patients(seed)[index], scenario)
    contract_loop.run()
    events = M.critical_events(contract_loop)
    counts: dict[str, int] = {}
    for e in events:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    event_rows = "".join(
        f"<tr><td>{E(e['clock'])}</td><td>{E(e['category'])}</td><td>{E(e['source'])}</td><td>{E(e['detail'])}</td></tr>"
        for e in events
        if e["category"] not in ("command", "confirmation")
    )
    body = [
        f"<h1>Generic Glycemic Loop: {E(scenario.name)}</h1>",
        f'<p class="banner"><strong>{E(views.DISCLAIMER)}</strong> All devices, organizations and numbers are '
        f"invented. bridge-island {E(bridge_island.__version__)}, seed {seed}.</p>",
        f"<p>{E(scenario.description)}</p>",
        "<h2>The architecture</h2><figure>"
        + architecture_svg()
        + "<figcaption>Fig. 1. Islands, Bridge parts and "
        "the environment.</figcaption></figure>",
        views.run_view(scenario.name, index, L.ARCHS, seed),
    ]
    if cohort:
        body.append(
            "<h2>Across the cohort</h2>" + views.cohort_view(scenario.name, L.ARCHS, seed, workers)
        )
    body += [
        f"<h2>Critical event record (bridge-contract, {E(patient)})</h2>",
        "<p>"
        + ", ".join(f"{n} {E(k)}" for k, n in sorted(counts.items()))
        + " events. Commands and confirmations "
        "are counted here and listed in the CSV the experiment writes; the rest are below.</p>",
        f"<table><caption>Malfunctions, alarms, acknowledgments and connectivity</caption><tr><th>Clock</th><th>Category</th>"
        f"<th>Source</th><th>Detail</th></tr>{event_rows}</table>"
        if event_rows
        else "<p>No malfunctions, alarms or connectivity events.</p>",
        "<h2>The contract</h2>" + views.contract_card(results=views.claim_results()),
        "<h2>Limitations and threats to validity</h2><ul>"
        + "".join(f"<li>{E(x)}</li>" for x in LIMITATIONS)
        + "</ul>",
    ]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
        f'content="width=device-width,initial-scale=1"><title>GGL report: {E(scenario.name)}</title>'
        f"<style>{CSS}{svg.CSS}.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.75rem}}"
        ".tile{border:1px solid var(--line);border-radius:6px;padding:.6rem;background:var(--card)}.tile .v{font-size:1.4rem;font-weight:700}"
        ".tile .k{color:var(--muted);font-size:.85rem}.pass{color:var(--ok);font-weight:700}.fail{color:var(--bad);font-weight:700}"
        '</style></head><body><a class="skip" href="#main">Skip to content</a><main id="main">'
        + "".join(body)
        + "</main></body></html>"
    )
