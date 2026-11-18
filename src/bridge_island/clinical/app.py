"""The app's "Insulin loop" tab: the same features as the main simulation, for the Generic Glycemic Loop.

Run a scenario (three steps, quick starts, your own faults, downloads), scenario files, the full
report, the guidelines G1 to G8, experiments, the contract card, the change ledger and a help page.

Server-rendered, like the rest of the app, so the same code runs in ``bridge-island ui`` and in the
in-browser build. Nothing typed is used as a path: scenarios and changes are chosen from the shipped
lists. Synthetic, illustrative; not medical software.
"""

from __future__ import annotations

import base64
import contextlib
import csv
import html
import io
import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

import bridge_island
from bridge_island.clinical import envelope as Env
from bridge_island.clinical import experiment as X
from bridge_island.clinical import explain, views
from bridge_island.clinical import loop as L
from bridge_island.clinical import scenarios as S
from bridge_island.examples import load_world
from bridge_island.rules import check_rules
from bridge_island.scenario import ScenarioError
from bridge_island.yamlutil import YamlError, load_yaml

E = html.escape

PAGES = [
    ("/insulin/tour", "Tour"),
    ("/insulin", "Run a scenario"),
    ("/insulin/scenarios", "Scenario files"),
    ("/insulin/report", "Full report"),
    ("/insulin/guidelines", "Guidelines"),
    ("/insulin/experiments", "Experiments"),
    ("/insulin/contract", "Contract card"),
    ("/insulin/changes", "Change ledger"),
    ("/insulin/help", "Help"),
]
MAX_CUSTOM_FAULTS = 2
BROWSER_PATIENTS = 2  # the in-browser build runs in one process, so keep the experiments short

FAULT_TEXT = {
    "sensor_swap": "Replace the glucose sensor with another maker's model (mmol/L, every minute)",
    "pump_swap": "Replace the pump with one on firmware v2 (renamed field, 0.05 U pulses)",
    "sensor_dropout": "The sensor sends nothing for a while",
    "sensor_delay": "Readings are held back and released in bursts, still carrying their old times",
    "sensor_value": "The sensor reports 600 mg/dL marked OK, then error codes",
    "pump_silent": "The pump stops confirming commands (and for the second half, delivering)",
    "replay_command": "An old dose command, still valid, is put back on the path to the pump",
    "display_throttle": "The phone's operating system holds the display app back",
    "bridge_stall": "The Bridge itself stops for a while (its single point of failure)",
    "meal_eaten": "An extra meal",
}
GUIDELINES = {
    "G1": "No device has a direct link to the controller host. Everything goes through the Bridge.",
    "G2": "Data is translated only where it needs to be. If both sides already agree, it passes through untouched.",
    "G3": "Every datum keeps the time it was true at, not the time it was delivered, so receivers can always tell how old it is.",
    "G4": "The Bridge only reads what the controller host publishes and only sends what it accepts. Anything else is rejected and logged.",
    "G5": "The mapping is compiled from a YAML file ahead of time; nothing is looked up at run time.",
    "G6": "The Bridge is one unit, with its parts kept apart inside it.",
    "G7": "Clinical fail-safe first: every state in which the Bridge loses valid glucose data maps to the contract's fail-safe output. The controller gets a FAILSAFE-flagged reading, and every command carries the fail-safe basal until valid data returns.",
    "G8": "Degradation is visible: every fail-safe or alarm state produces an annunciation that reaches the phone display or, if the display does not acknowledge it, is logged as undelivered with its age.",
}
EXPERIMENTS = {
    "closed-loop": (
        "Closed loop",
        "Every scenario, the three architectures and a number of virtual patients, with the same meals, seeds and faults in each. "
        "Gives minutes in range, patients below 54, whether anyone was told, and the figures.",
        True,
    ),
    "change-ledger": (
        "Change ledger",
        "The ten declared peripheral changes against the three architectures: how many components change, how many are re-verified, "
        "which organizations must be involved, and the envelope decision for each. Counts follow a stated model; they are not measurements.",
        False,
    ),
}
TERMS = [
    (
        "Generic Glycemic Loop",
        "An invented closed-loop insulin system: a controller host you cannot change, sensors, pumps and a phone display from other makers, and an invented patient model.",
    ),
    (
        "Controller host",
        "The platform that decides doses. Someone else built and certified it, so it is the Baseline Island: you can use what it publishes and nothing more.",
    ),
    (
        "Devices",
        "Two glucose sensors (A, B), two pumps (A, B) and a phone display. Each speaks its own units and rates and changes on its own schedule: the Unique Island.",
    ),
    (
        "direct",
        "Every device is wired to the controller host through its own adapter. Each adapter carries its own translation and range checks.",
    ),
    (
        "bridge-basic",
        "Devices go through the Bridge, which translates units, formats and names, stamps source times and rejects what the host does not accept.",
    ),
    (
        "bridge-contract",
        "The Bridge plus a contract: maximum data age, a fail-safe output when glucose is lost, command ids, confirmations and annunciations to the display.",
    ),
    (
        "Contract card",
        "One page holding the contract: owner, data items, timing, failure behaviour, security, claims, change envelope and what the Bridge cannot assure.",
    ),
    (
        "Change envelope",
        "The set of peripheral changes the Bridge can absorb by editing its mapping alone. A change outside it needs the owners of other parts to review.",
    ),
    (
        "Fail-safe",
        "What the controller does when glucose data is too old or invalid: the programmed basal instead of a dose computed from stale data.",
    ),
    (
        "Told / logged",
        "Told: the controller or display was informed of the fault. Logged: it was only written down inside the Bridge.",
    ),
    (
        "Unflagged stale glucose",
        "Minutes in which the controller acted on a reading that was old but not marked as old.",
    ),
    (
        "Time in range",
        "Minutes with glucose between 70 and 180 mg/dL. Below 54 mg/dL is the severe low used for harm counts.",
    ),
    (
        "Virtual patient",
        "One of 30 invented patients (VP01 to VP30) with different parameters and meal times. Not a model of any real person.",
    ),
]
NOT_MEDICAL = "Synthetic, illustrative. Not medical software."


def _first(form: Mapping[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key) or [default]
    return values[0]


def _num(
    form: Mapping[str, list[str]], key: str, default: float, low: float, high: float, label: str
) -> float:
    raw = _first(form, key, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        raise ScenarioError(f"{label} must be a number, you entered {raw!r}") from None
    if not low <= value <= high:
        raise ScenarioError(f"{label} must be between {low:g} and {high:g}, you entered {value:g}")
    return value


def _sub_nav(active: str = "") -> str:
    links = "".join(
        f'<a href="{h}"{" aria-current=page" if h == active else ""}>{E(t)}</a>' for h, t in PAGES
    )
    return f'<p class="presets" role="navigation" aria-label="Insulin loop">{links}</p>'


def _select(name: str, options: list[tuple[str, str]], selected: str) -> str:
    opts = "".join(
        f'<option value="{E(v)}"{" selected" if v == selected else ""}>{E(t)}</option>'
        for v, t in options
    )
    return f'<select id="{name}" name="{name}">{opts}</select>'


def _head(title: str, active: str, lede: str = "") -> str:
    return f'<h1>{E(title)}</h1><p class="banner">{E(views.DISCLAIMER)}</p>{_sub_nav(active)}{lede}'


def _shipped_yaml() -> dict[str, tuple[str, str]]:
    out = {}
    for p in S.shipped():
        text = p.read_text(encoding="utf-8")
        data = load_yaml(text) or {}
        out[p.stem] = (str(data.get("description", "")), text)
    return out


# ---- run a scenario ----------------------------------------------------------------------


def _patients(app: Any) -> list[tuple[str, str]]:
    patients = [(f"VP{i:02d}", f"Virtual patient VP{i:02d}") for i in range(1, 31)]
    patients.append(
        ("cohort", "All 30 (desktop only)" if app.browser else "All 30 virtual patients")
    )
    return patients


def _fault_rows(q: Mapping[str, list[str]]) -> str:
    kinds = [("", "(none)")] + [(k, k.replace("_", " ")) for k in L.FAULT_KINDS]
    rows = []
    for i in range(1, MAX_CUSTOM_FAULTS + 1):
        rows.append(
            f'<div class="row"><div><label for="f{i}_kind">Extra fault {i}</label>{_select(f"f{i}_kind", kinds, _first(q, f"f{i}_kind"))}</div>'
            f'<div><label for="f{i}_at">Starts at</label><input id="f{i}_at" name="f{i}_at" value="{E(_first(q, f"f{i}_at", "12:00"))}"></div>'
            f'<div><label for="f{i}_minutes">Lasts (minutes)</label><input id="f{i}_minutes" name="f{i}_minutes" type="number" min="0" max="1440" step="any" value="{E(_first(q, f"f{i}_minutes", "30"))}"></div></div>'
        )
    return "".join(rows)


def run_form(app: Any, q: Mapping[str, list[str]]) -> str:
    shipped = _shipped_yaml()
    chosen = _first(q, "scenario", "sensor_swap_units")
    quick = "".join(f'<a href="/insulin?scenario={E(n)}">{E(n)}</a>' for n in shipped)
    checked = q.get("arch") or list(L.ARCHS)
    checks = "".join(
        f'<label class="check"><input type="checkbox" name="arch" value="{a}"{" checked" if a in checked else ""}> {E(a)}</label>'
        for a in L.ARCHS
    )
    described = "".join(
        f"<dt>{E(n)}</dt><dd>{E(views.EXPLAIN.get(n, d))}</dd>" for n, (d, _) in shipped.items()
    )
    options = [(n, n) for n in shipped]
    return f"""<form class="steps" method="post" action="/insulin">{app._hidden()}
<section class="step" aria-labelledby="s1"><h2 id="s1"><span class="num" aria-hidden="true">1</span>Choose who and what</h2>
<div class="row">
<div><label for="patient">Who</label>{_select("patient", _patients(app), _first(q, "patient", "VP01"))}
<p class="hint">One patient takes a second or two; all 30 take about a minute.</p></div>
<div><label for="version">Controller host version</label>{_select("version", [("1", "Version 1"), ("2", "Version 2 (a change from version 1)")], _first(q, "version", "1"))}
<p class="hint">Version 2 renames, converts and removes some elements. The contract is written for version 1, so it refuses the version 2 basal: that is the point of the change ledger.</p></div></div>
<fieldset><legend>Ways of connecting the devices</legend>{checks}
<p class="hint">Compare all three, or pick one.</p></fieldset></section>
<section class="step" aria-labelledby="s2"><h2 id="s2"><span class="num" aria-hidden="true">2</span>Decide what goes wrong</h2>
<p class="hint">Start from a ready-made scenario, then add faults of your own if you like.</p>
<div class="presets" role="group" aria-label="Quick starts"><strong>Quick starts:</strong> {quick}</div>
<div class="row"><div><label for="scenario">Scenario</label>{_select("scenario", options, chosen)}</div></div>
<fieldset><legend>Extra faults (optional)</legend>{_fault_rows(q)}
<p class="hint">Time is HH:MM, or a meal plus minutes such as lunch+15 (breakfast, lunch, snack, dinner). Each extra fault is added to the scenario's own. Fault kinds are explained on the Help page.</p></fieldset></section>
<section class="step" aria-labelledby="s3"><h2 id="s3"><span class="num" aria-hidden="true">3</span>Run it</h2>
<div class="row"><div><label for="duration_h">How long (hours of simulated time)</label><input id="duration_h" name="duration_h" type="number" min="1" max="{S.MAX_HOURS:g}" step="any" value="{E(_first(q, "duration_h", "24"))}"></div>
<div><label for="seed">Random seed</label><input id="seed" name="seed" type="number" step="1" value="{E(_first(q, "seed", "1"))}"><p class="hint">The same seed always gives the same day.</p></div></div>
<button type="submit">Run the simulation</button>
<p class="hint">The result explains what happened in plain sentences. Nothing is saved unless you download it.</p></section></form>
<h2>The scenarios</h2><dl>{described}</dl>"""


def _scenario_data(form: Mapping[str, list[str]], archs: list[str], who: str) -> dict[str, Any]:
    name = _first(form, "scenario")
    shipped = _shipped_yaml()
    if name not in shipped:
        raise ScenarioError("Choose a scenario from the list.")
    data = dict(load_yaml(shipped[name][1]) or {})
    faults = list(data.get("faults") or [])
    for i in range(1, MAX_CUSTOM_FAULTS + 1):
        kind = _first(form, f"f{i}_kind")
        if kind:
            faults.append(
                {
                    "kind": kind,
                    "at": _first(form, f"f{i}_at", "12:00").strip(),
                    "minutes": _num(form, f"f{i}_minutes", 30, 0, 1440, f"Extra fault {i} length"),
                }
            )
    if len(faults) != len(data.get("faults") or []):
        data["name"] = f"{name}_plus_your_faults"
    data["faults"] = faults
    data["duration_h"] = _num(form, "duration_h", 24, 1, S.MAX_HOURS, "How long")
    data["seed"] = int(_num(form, "seed", 1, -(10**9), 10**9, "Random seed"))
    data["patients"] = "all" if who == "cohort" else who
    data["architectures"] = archs
    return data


def _glucose_csv(loops: Mapping[str, Any]) -> str:
    buffer = io.StringIO()
    buffer.write("# synthetic, illustrative\n")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["minute", *loops])
    traces = [lp.patient.trace for lp in loops.values()]
    for minute in range(min(map(len, traces))):
        writer.writerow([minute, *(f"{t[minute]:.1f}" for t in traces)])
    return buffer.getvalue()


def _downloads(app: Any, files: dict[str, tuple[str, bytes]]) -> str:
    run_id = app._store(files)
    labels = {
        "summary.json": "Numbers (JSON)",
        "glucose.csv": "Glucose every minute (CSV)",
        "scenario.yaml": "These settings as a scenario file",
        "manifest.json": "Reproducibility record",
    }
    links = "".join(
        f'<a class="button secondary" href="/download/{run_id}/{n}">{E(labels.get(n, n))}</a>'
        for n in files
    )
    return f"<h2>Keep the results</h2><p>{links}</p>"


def _run_scenario(
    app: Any,
    data: dict[str, Any],
    version: int,
    again: str,
    title: str,
    active: str = "/insulin",
) -> tuple[str, str]:
    scenario, who, archs = S.parse(data, "the form")
    if version not in L.world().VERSIONS:
        raise ScenarioError("Choose a controller host version from the list.")
    scenario_yaml = yaml.safe_dump(data, sort_keys=False)
    if who == "all":
        if app.browser:
            raise ScenarioError(
                "All 30 patients is desktop only: run bridge-island ui on your computer, or pick one patient."
            )
        if version != 1:
            raise ScenarioError("Version 2 runs one patient at a time: pick a patient.")
        body = views.cohort_view(scenario, archs, scenario.seed, workers=1)
        files: dict[str, tuple[str, bytes]] = {}
    else:
        index = int(who[2:]) - 1
        body = views.run_view(scenario, index, archs, scenario.seed, version)
        run = views.single_run(scenario, index, archs, scenario.seed, version)
        files = {
            "summary.json": (
                "application/json",
                json.dumps(
                    {
                        "note": views.DISCLAIMER,
                        "scenario": scenario.name,
                        "patient": who,
                        "rows": run["rows"],
                    },
                    indent=2,
                    default=str,
                ).encode(),
            ),
            "glucose.csv": ("text/csv; charset=utf-8", _glucose_csv(run["loops"]).encode()),
        }
    files["scenario.yaml"] = ("text/yaml; charset=utf-8", scenario_yaml.encode())
    files["manifest.json"] = (
        "application/json",
        json.dumps(
            {
                "bridge_island_version": bridge_island.__version__,
                "python": ".".join(map(str, sys.version_info[:3])),
                "host_baseline_version": version,
                "scenario": data,
                "note": "synthetic, illustrative",
            },
            indent=2,
            default=str,
        ).encode(),
    )
    heading = f"<h1>Result: {E(scenario.name)}</h1>{_sub_nav(active)}"
    return title, heading + body + _downloads(
        app, files
    ) + "<h2>Change something and run again</h2>" + again


# ---- scenario files ----------------------------------------------------------------------


def scenarios_page(app: Any) -> str:
    shipped = _shipped_yaml()
    options = [(n, n) for n in shipped]
    details = "".join(
        f"<details><summary>{E(n)}</summary><p>{E(d)}</p><pre>{E(t)}</pre></details>"
        for n, (d, t) in shipped.items()
    )
    sample = next(iter(shipped.values()), ("", ""))[1]
    return f"""{_head("Scenario files", "/insulin/scenarios", "<p>A scenario file describes one day completely, so that it can be shared and repeated exactly. Run a shipped file, or paste your own. The same files run from the command line with <code>bridge-island run</code>.</p>")}
<form class="card" method="post" action="/insulin/scenarios">{app._hidden()}
<h2>Run a shipped scenario</h2><label for="name">Scenario</label>{_select("name", options, "")}
<p><button type="submit">Run this scenario</button></p></form>
<form class="card" method="post" action="/insulin/scenarios">{app._hidden()}
<h2>Or paste your own</h2><p class="hint">The format is explained on the Help page. You can also load a file from your computer into the box.</p>
<label for="file">Load a file into the box</label><input id="file" type="file" accept=".yaml,.yml,.txt">
<label for="yaml">Scenario text</label><textarea id="yaml" name="yaml" spellcheck="false">{E(sample)}</textarea>
<p><button type="submit">Run the pasted scenario</button></p></form>
<h2>The shipped files</h2>{details}
<script>document.getElementById('file').addEventListener('change',function(e){{var f=e.target.files[0];if(!f)return;
var r=new FileReader();r.onload=function(){{document.getElementById('yaml').value=r.result}};r.readAsText(f)}});</script>"""


def _run_scenario_file(app: Any, form: Mapping[str, list[str]]) -> tuple[str, str]:
    text = _first(form, "yaml")
    if not text.strip():
        shipped = _shipped_yaml()
        name = _first(form, "name")
        if name not in shipped:  # only names from the shipped list are accepted, never a path
            raise ScenarioError("Choose a scenario from the list.")
        text = shipped[name][1]
    try:
        data = load_yaml(text)
    except YamlError as exc:
        raise ScenarioError(f"The scenario is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError("The scenario must be a mapping at the top level.")
    note = ""
    if data.get("patients", "all") == "all" and app.browser:
        data = {**data, "patients": "VP01"}
        note = '<p class="hint">The in-browser build runs one patient: VP01 was used instead of all 30.</p>'
    title, body = _run_scenario(app, data, 1, "", "Result", "/insulin/scenarios")
    return title, body.replace("</p>", f"</p>{note}", 1) if note else body


# ---- full report -------------------------------------------------------------------------


def report_form(app: Any, q: Mapping[str, list[str]]) -> str:
    names = [(p.stem, p.stem) for p in S.shipped()]
    cohort = (
        '<p class="hint">The 30-patient table is left out in the in-browser build.</p>'
        if app.browser
        else '<label class="check"><input type="checkbox" name="cohort" value="1" checked> Include the 30-patient table (about a minute)</label>'
    )
    return f"""{_head("Full report", "/insulin/report", "<p>One page with everything for a scenario: the architecture, one patient's glucose and insulin, the critical event record, the contract card and the limitations. Open it here or download it as a single file that works offline.</p>")}
<form class="card" method="post" action="/insulin/report">{app._hidden()}
<div class="row"><div><label for="scenario">Scenario</label>{_select("scenario", names, _first(q, "scenario", "sensor_swap_units"))}</div>
<div><label for="patient">Patient for the charts</label>{_select("patient", _patients(app)[:30], _first(q, "patient", "VP01"))}</div>
<div><label for="seed">Random seed</label><input id="seed" name="seed" type="number" step="1" value="{E(_first(q, "seed", "1"))}"></div></div>
{cohort}
<button type="submit" name="mode" value="open">Open the report</button>
<button type="submit" name="mode" value="download" class="secondary">Download as a file</button></form>"""


def _report(app: Any, form: Mapping[str, list[str]]) -> tuple[str, str, dict[str, str]]:
    from bridge_island.clinical.report import build_report

    name = _first(form, "scenario")
    if name not in {p.stem for p in S.shipped()}:
        raise ScenarioError("Choose a scenario from the list.")
    patient = _first(form, "patient", "VP01")
    if not (
        len(patient) == 4
        and patient.startswith("VP")
        and patient[2:].isdigit()
        and 1 <= int(patient[2:]) <= 30
    ):
        raise ScenarioError("Choose a patient from the list.")
    seed = int(_num(form, "seed", 1, -(10**9), 10**9, "Random seed"))
    cohort = bool(_first(form, "cohort")) and not app.browser
    page = build_report(name, seed, patient, cohort=cohort, workers=1)
    if _first(form, "mode") == "download":
        return (
            "",
            page,
            {
                "Content-Disposition": f'attachment; filename="bridge-island-insulin-loop-{name}.html"'
            },
        )
    bar = '<p><a href="/insulin/report">&larr; Back to bridge-island</a></p>'
    return "", page.replace('<main id="main">', f'<main id="main">{bar}', 1), {}


# ---- guidelines --------------------------------------------------------------------------


def guidelines_form(app: Any) -> str:
    explain = "".join(f"<dt>{k}</dt><dd>{E(v)}</dd>" for k, v in GUIDELINES.items())
    return f"""{_head("Check the guidelines", "/insulin/guidelines", "<p>The design rests on eight guidelines: the six of the main simulation, and two for the insulin loop. This runs a test for each one on the loop and says pass or fail.</p>")}
<form class="card" method="post" action="/insulin/guidelines">{app._hidden()}
<button type="submit">Check the eight guidelines</button></form>
<h2>What the guidelines say</h2><dl>{explain}</dl>"""


def _guidelines() -> tuple[str, str]:
    results = check_rules(load_world("insulin-loop"))
    rows = "".join(
        f"<tr><td>{E(r.rule)}</td><td>{E(r.title)}</td><td>{views.badge(r.passed)}</td><td>{E(r.detail)}</td></tr>"
        for r in results
    )
    ok = sum(r.passed for r in results)
    return (
        "Guidelines",
        f"<h1>Guidelines: insulin loop</h1>{_sub_nav('/insulin/guidelines')}"
        f'<p class="banner"><strong>{ok} of {len(results)} pass.</strong> {E(views.DISCLAIMER)} G7 and G8 are tested on the bridge-contract architecture and are not claimed for host version 2.</p>'
        "<table><caption>Each guideline and what the test found</caption>"
        f"<tr><th>Rule</th><th>Guideline</th><th>Result</th><th>What was found</th></tr>{rows}</table>"
        '<p><a class="button secondary" href="/insulin/guidelines">Check again</a></p>',
    )


# ---- experiments -------------------------------------------------------------------------


def experiments_form(app: Any) -> str:
    cards = []
    for key, (title, text, heavy) in EXPERIMENTS.items():
        patients = ""
        if heavy:
            high = BROWSER_PATIENTS if app.browser else 30
            patients = (
                f'<div><label for="n-{key}">Virtual patients</label><input id="n-{key}" name="patients" type="number" min="1" max="{high}" value="{min(3, high)}">'
                f'<p class="hint">{"Up to 2 in the browser." if app.browser else "All 30 take several minutes."} Ten scenarios, three architectures each.</p></div>'
            )
        cards.append(
            f"""<form class="card" method="post" action="/insulin/experiments">{app._hidden()}
<h2>{E(title)}</h2><p>{E(text)}</p><input type="hidden" name="name" value="{key}">
<div class="row"><div><label for="seed-{key}">Random seed</label><input id="seed-{key}" name="seed" type="number" value="1"></div>{patients}</div>
<button type="submit">Run</button></form>"""
        )
    return _head(
        "Experiments",
        "/insulin/experiments",
        "<p>Two repeatable experiments. Each gives a table you can read and files you can download.</p>",
    ) + "".join(cards)


def _experiment(app: Any, form: Mapping[str, list[str]]) -> tuple[str, str]:
    name = _first(form, "name")
    if name not in EXPERIMENTS:
        raise ScenarioError("Choose one of the experiments.")
    seed = int(_num(form, "seed", 1, -(10**9), 10**9, "Random seed"))
    buffer = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buffer):
        if name == "closed-loop":
            high = BROWSER_PATIENTS if app.browser else 30
            n = int(_num(form, "patients", 3, 1, high, "Virtual patients"))
            files = X.closed_loop(Path(tmp), seed, patients=range(n), workers=1)
        else:
            files = X.change_ledger(Path(tmp), seed)
        stored: dict[str, tuple[str, bytes]] = {}
        images = []
        for path in files.values():
            data = path.read_bytes()
            if path.suffix == ".png":
                images.append(
                    f'<img class="plot" alt="{E(path.stem.replace("_", " "))} (synthetic, illustrative)" src="data:image/png;base64,{base64.b64encode(data).decode()}">'
                )
            stored[path.name] = (
                "image/png" if path.suffix == ".png" else "text/csv; charset=utf-8",
                data,
            )
    run_id = app._store(stored)
    links = "".join(
        f'<a class="button secondary" href="/download/{run_id}/{E(n)}">{E(n)}</a>' for n in stored
    )
    text = "\n".join(
        line for line in buffer.getvalue().splitlines() if not line.startswith("wrote ")
    )
    title = EXPERIMENTS[name][0]
    return (
        title,
        f'<h1>{E(title)}: insulin loop</h1>{_sub_nav("/insulin/experiments")}<p class="banner">{E(views.DISCLAIMER)} Seed {seed}.</p>'
        f"<pre>{E(text)}</pre>{''.join(images)}<h2>Files</h2><p>{links}</p>"
        '<p><a class="button secondary" href="/insulin/experiments">Run another</a></p>',
    )


# ---- help --------------------------------------------------------------------------------


def help_page() -> str:
    terms = "".join(f"<dt>{E(t)}</dt><dd>{E(d)}</dd>" for t, d in TERMS)
    faults = "".join(f"<dt><code>{E(k)}</code></dt><dd>{E(v)}</dd>" for k, v in FAULT_TEXT.items())
    return f"""{_head("Help: insulin loop", "/insulin/help")}
<h2>What is this?</h2>
<p>The same idea as the main simulation, applied to an invented closed-loop insulin system: a controller host that you cannot change,
devices from other makers, and one layer between them. You choose a day, add faults, and see what each way of connecting them does to
glucose and to who is told. It is not medical software, not a validated patient model and not for clinical use.</p>
<h2>Where do I start?</h2>
<ol><li>On <a href="/insulin">Run a scenario</a>, press <em>Run</em> with the defaults, or pick a quick start such as <em>pump_silent</em>.</li>
<li>Read <em>What happened</em> at the top of the result, then the glucose chart and the event timeline.</li>
<li>Try the <a href="/insulin/guidelines">guidelines</a>, the <a href="/insulin/contract">contract card</a> and the <a href="/insulin/changes">change ledger</a>.</li></ol>
<h2>Reading the charts</h2>
<p>The glucose chart shows one line per architecture across the day; shaded bands are fail-safe periods and marks show when a fault starts.
Where two architectures give the same glucose their lines lie on top of each other. The insulin strip shows each delivered bolus. Lines differ by
style as well as colour, so colour is never the only clue.</p>
<h2>Fault kinds</h2><dl>{faults}</dl>
<h2>Scenario files</h2>
<pre>name: my_run
example: insulin-loop
description: What this run is about.
duration_h: 24
seed: 1
patients: VP01          # all, or one id VP01..VP30
architectures: [direct, bridge-basic, bridge-contract]
faults:
  - {{kind: sensor_dropout, at: lunch+15, minutes: 45}}</pre>
<p><code>at</code> is a clock time such as <code>08:00</code> or a meal plus minutes such as <code>lunch+15</code>. On a result page,
<em>These settings as a scenario file</em> downloads the file for the run you just made.</p>
<h2>Words used</h2><dl>{terms}</dl>"""


# ---- routing -----------------------------------------------------------------------------


def get(app: Any, path: str, query: Mapping[str, list[str]]) -> tuple[str, str]:
    if path == "/insulin":
        return "Insulin loop", (
            _head(
                "Insulin loop",
                "/insulin",
                '<p class="lede">The Generic Glycemic Loop: an invented controller host, invented sensors, pumps and a phone display, and an invented patient model. '
                "Run a day with a fault and compare three ways of connecting them. New here? Take the <a href='/insulin/tour'>tour</a> first. Three steps, then a plain-English result.</p>",
            )
            + run_form(app, query)
        )
    if path == "/insulin/tour":
        return "Tour", _head("Tour: how it works", "/insulin/tour") + explain.tour_page()
    if path == "/insulin/scenarios":
        return "Scenario files", scenarios_page(app)
    if path == "/insulin/report":
        return "Full report", report_form(app, query)
    if path == "/insulin/guidelines":
        return "Guidelines", guidelines_form(app)
    if path == "/insulin/experiments":
        return "Experiments", experiments_form(app)
    if path == "/insulin/help":
        return "Help", help_page()
    if path == "/insulin/contract":
        results = views.claim_results()
        return (
            "Contract card",
            f"<h1>Contract card</h1>{_sub_nav('/insulin/contract')}"
            + views.contract_card(results=results),
        )
    if path == "/insulin/changes":
        return "Change ledger", change_form(app)
    return "Not found", '<h1>Page not found</h1><p><a href="/insulin">Back</a></p>'


def change_form(app: Any, selected: str = "") -> str:
    changes = Env.shipped_changes()
    options = [(c.path.stem, f"{c.path.stem}: {c.description}") for c in changes if c.path]
    return f"""<h1>Change ledger</h1>{_sub_nav("/insulin/changes")}
<p>Pick a declared change to a peripheral. The envelope check says whether the Bridge absorbs it on its own (and which
conformance tests to re-run), or which owners must review it.</p>
<form class="card" method="post" action="/insulin/changes">{app._hidden()}
<label for="change">Change</label>{_select("change", options, selected or options[0][0])}
<p><button type="submit">Check this change</button></p></form>"""


def post(
    app: Any, path: str, form: Mapping[str, list[str]]
) -> tuple[str, str] | tuple[str, str, dict[str, str]]:
    """A page ``(title, body)``, or ``("", whole document, headers)`` for the report."""
    if path == "/insulin/changes":
        stem = _first(form, "change")
        if stem not in {c.path.stem for c in Env.shipped_changes() if c.path}:
            raise ScenarioError("Choose a change from the list.")
        return "Change ledger", change_form(app, stem) + views.ledger_view(stem)
    if path == "/insulin/scenarios":
        return _run_scenario_file(app, form)
    if path == "/insulin/report":
        return _report(app, form)
    if path == "/insulin/guidelines":
        return _guidelines()
    if path == "/insulin/experiments":
        return _experiment(app, form)
    if path != "/insulin":
        raise ScenarioError("Unknown insulin-loop page.")
    archs = [a for a in L.ARCHS if a in form.get("arch", [])]
    if not archs:
        raise ScenarioError("Choose at least one architecture.")
    who = _first(form, "patient", "VP01")
    if who != "cohort" and not (
        len(who) == 4 and who.startswith("VP") and who[2:].isdigit() and 1 <= int(who[2:]) <= 30
    ):
        raise ScenarioError("Choose a patient from the list.")
    version = int(_num(form, "version", 1, 1, 99, "Controller host version"))
    data = _scenario_data(form, archs, who)
    again = run_form(app, form)
    return _run_scenario(app, data, version, again, f"Result: {data['name']}")
