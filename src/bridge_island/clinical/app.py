"""The app's "Insulin loop" pages: run view, contract card and change ledger.

Server-rendered, like the rest of the app, so the same code runs in ``bridge-island ui`` and in the
in-browser build. Nothing typed is used as a path: scenarios and changes are chosen from the shipped
lists. Synthetic, illustrative; not medical software.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from bridge_island.clinical import envelope as Env
from bridge_island.clinical import loop as L
from bridge_island.clinical import scenarios as S
from bridge_island.clinical import views
from bridge_island.scenario import ScenarioError

E = html.escape


def _first(form: Mapping[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key) or [default]
    return values[0]


def _sub_nav() -> str:
    return (
        '<p class="presets"><a href="/insulin">Run a scenario</a><a href="/insulin/contract">Contract card</a>'
        '<a href="/insulin/changes">Change ledger</a></p>'
    )


def _select(name: str, options: list[tuple[str, str]], selected: str) -> str:
    opts = "".join(
        f'<option value="{E(v)}"{" selected" if v == selected else ""}>{E(t)}</option>'
        for v, t in options
    )
    return f'<select id="{name}" name="{name}">{opts}</select>'


def run_form(app: Any, q: Mapping[str, list[str]]) -> str:
    names = [p.stem for p in S.shipped()]
    patients = [(f"VP{i:02d}", f"Virtual patient VP{i:02d}") for i in range(1, 31)]
    patients.append(
        ("cohort", "All 30 (desktop only)" if app.browser else "All 30 virtual patients")
    )
    checks = "".join(
        f'<label class="check"><input type="checkbox" name="arch" value="{a}" checked> {E(a)}</label>'
        for a in L.ARCHS
    )
    described = "".join(f"<dt>{E(n)}</dt><dd>{E(views.EXPLAIN.get(n, ''))}</dd>" for n in names)
    return f"""<h1>Insulin loop</h1>
<p class="lede">The Generic Glycemic Loop: an invented controller host, invented sensors, pumps and a phone display, and
an invented patient model. Run a day with a fault and compare three ways of connecting them.</p>
<p class="banner">{E(views.DISCLAIMER)}</p>{_sub_nav()}
<form class="card" method="post" action="/insulin">{app._hidden()}
<div class="row"><div><label for="scenario">Scenario</label>{_select("scenario", [(n, n) for n in names], _first(q, "scenario", "sensor_swap_units"))}</div>
<div><label for="patient">Who</label>{_select("patient", patients, _first(q, "patient", "VP01"))}
<p class="hint">One patient takes a few seconds; all 30 take about a minute.</p></div></div>
<fieldset><legend>Architectures</legend>{checks}</fieldset>
<button type="submit">Run 24 hours</button></form>
<h2>The scenarios</h2><dl>{described}</dl>"""


def get(app: Any, path: str, query: Mapping[str, list[str]]) -> tuple[str, str]:
    if path == "/insulin":
        return "Insulin loop", run_form(app, query)
    if path == "/insulin/contract":
        results = views.claim_results()
        return "Contract card", f"<h1>Contract card</h1>{_sub_nav()}" + views.contract_card(
            results=results
        )
    if path == "/insulin/changes":
        return "Change ledger", change_form(app)
    return "Not found", '<h1>Page not found</h1><p><a href="/insulin">Back</a></p>'


def change_form(app: Any, selected: str = "") -> str:
    changes = Env.shipped_changes()
    options = [(c.path.stem, f"{c.path.stem}: {c.description}") for c in changes if c.path]
    return f"""<h1>Change ledger</h1>{_sub_nav()}
<p>Pick a declared change to a peripheral. The envelope check says whether the Bridge absorbs it on its own (and which
conformance tests to re-run), or which owners must review it.</p>
<form class="card" method="post" action="/insulin/changes">{app._hidden()}
<label for="change">Change</label>{_select("change", options, selected or options[0][0])}
<p><button type="submit">Check this change</button></p></form>"""


def post(app: Any, path: str, form: Mapping[str, list[str]]) -> tuple[str, str]:
    if path == "/insulin/changes":
        stem = _first(form, "change")
        if stem not in {c.path.stem for c in Env.shipped_changes() if c.path}:
            raise ScenarioError("Choose a change from the list.")
        return "Change ledger", change_form(app, stem) + views.ledger_view(stem)
    if path != "/insulin":
        raise ScenarioError("Unknown insulin-loop page.")
    scenario = _first(form, "scenario")
    if scenario not in {p.stem for p in S.shipped()}:
        raise ScenarioError("Choose a scenario from the list.")
    archs = [a for a in L.ARCHS if a in form.get("arch", [])]
    if not archs:
        raise ScenarioError("Choose at least one architecture.")
    who = _first(form, "patient", "VP01")
    again = run_form(app, {"scenario": [scenario], "patient": [who]})
    if who == "cohort":
        if app.browser:
            raise ScenarioError(
                "All 30 patients is desktop only: run bridge-island ui on your computer, or pick one patient."
            )
        body = views.cohort_view(scenario, archs, workers=1)
    elif len(who) == 4 and who.startswith("VP") and who[2:].isdigit() and 1 <= int(who[2:]) <= 30:
        body = views.run_view(scenario, int(who[2:]) - 1, archs)
    else:
        raise ScenarioError("Choose a patient from the list.")
    return (
        f"Result: {scenario}",
        f"<h1>Result: {E(scenario)}</h1>{_sub_nav()}{body}<h2>Run another</h2>"
        + again.split("</p>", 2)[-1],
    )
