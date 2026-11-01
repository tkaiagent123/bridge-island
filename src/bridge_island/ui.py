"""A local web app for operating bridge-island without a command line.

Run it with ``bridge-island ui`` (or double-click ``Start-bridge-island.bat``). It serves one page
set from your own machine, in your browser. It needs nothing beyond the standard library.

Safety: the server binds to 127.0.0.1 only; every form carries a per-session token; the Host header
is checked; nothing you type is ever used as a file path (scenarios are chosen from the shipped
list or pasted as text, and results are served from memory).
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import html
import io
import json
import secrets
import tempfile
import threading
import traceback
import webbrowser
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

import bridge_island
from bridge_island import experiments
from bridge_island.examples import EXAMPLES, examples_dir, load_world
from bridge_island.report import CSS as REPORT_CSS
from bridge_island.report import age_charts, build_report, table
from bridge_island.rules import check_rules
from bridge_island.scenario import (
    Scenario,
    ScenarioError,
    fault_messages,
    manifest,
    parse_scenario,
    parse_scenario_text,
    run_scenario,
)
from bridge_island.summary import findings, summarize, trace_csv_text, trace_rows
from bridge_island.system import System
from bridge_island.yamlutil import YamlError, load_yaml

E = html.escape
MAX_DURATION_S = 120.0
MAX_TRIALS = 2000
MAX_BODY = 256 * 1024
KEEP_RESULTS = 12
NOTE = "All content is invented. Numbers are synthetic, illustrative."

NAV = [
    ("/", "Run a simulation"),
    ("/scenarios", "Scenario files"),
    ("/report", "Full report"),
    ("/rules", "Check the guidelines"),
    ("/experiments", "Experiments"),
    ("/help", "Help"),
]

EXTRA_CSS = """
:root{--btn:#0969da;--btn-fg:#fff}
@media (prefers-color-scheme:dark){:root{--btn:#1f6feb}}
header.top{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem 1rem}
nav{display:flex;flex-wrap:wrap;gap:.35rem;margin:.5rem 0 1rem}
nav a{padding:.45rem .8rem;border:1px solid var(--line);border-radius:6px;text-decoration:none;color:var(--fg)}
nav a[aria-current=page]{background:var(--fg);color:var(--bg);border-color:var(--fg)}
nav a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,
summary:focus-visible,a:focus-visible{outline:3px solid var(--s0);outline-offset:2px}
.card{border:1px solid var(--line);border-radius:8px;padding:1rem 1.25rem;background:var(--card);margin:1rem 0}
.card h2,.card h3{margin-top:0;border:0}
label{display:block;font-weight:600;margin:.7rem 0 .15rem}
.hint{color:var(--muted);font-size:.88rem;margin:.1rem 0 .3rem}
input,select,textarea{font:inherit;padding:.45rem .55rem;border:1px solid var(--muted);border-radius:6px;
background:var(--bg);color:var(--fg);max-width:100%}
input[type=number]{width:7.5rem}textarea{width:100%;min-height:15rem;font-family:ui-monospace,Consolas,monospace;font-size:.88rem}
.row{display:flex;flex-wrap:wrap;gap:0 1.5rem}.row>div{flex:1 1 13rem}
fieldset{border:1px solid var(--line);border-radius:6px;margin:.9rem 0;padding:.4rem 1rem .8rem}
legend{font-weight:600;padding:0 .4rem}
.check{display:flex;gap:.5rem;align-items:center;font-weight:600;margin:.4rem 0}
.check input{width:1.2rem;height:1.2rem}
button,a.button{display:inline-block;font:inherit;font-weight:600;padding:.65rem 1.3rem;border:0;border-radius:6px;
background:var(--btn);color:var(--btn-fg);cursor:pointer;text-decoration:none;margin:.5rem .5rem 0 0}
button.secondary,a.button.secondary{background:transparent;color:var(--fg);border:1px solid var(--muted)}
.presets a{display:inline-block;margin:.2rem .4rem .2rem 0;padding:.35rem .7rem;border:1px solid var(--line);
border-radius:999px;text-decoration:none;color:var(--fg);background:var(--bg)}
.error{border:2px solid var(--bad);border-radius:8px;padding:.75rem 1rem;margin:1rem 0}
.error strong{color:var(--bad)}
ul.found li{margin:.35rem 0}
.pass{color:var(--ok);font-weight:700}.fail{color:var(--bad);font-weight:700}
details{margin:.5rem 0}summary{cursor:pointer;font-weight:600}
dt{font-weight:600;margin-top:.7rem}dd{margin:0 0 .3rem 1.2rem;max-width:70ch}
img.plot{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}
a{color:var(--btn)}
"""

PRESETS = [
    ("Normal run", {}),
    ("The Bridge stalls for 1.5 s", {"stall": "1", "stall_at": "2.2", "stall_for": "1.5"}),
    ("Baseline v2 removes an element", {"version": "2"}),
    ("A faulty system sends bad messages", {"fault": "out_of_range", "fault_at": "2"}),
    (
        "Stamp data with delivery time (the error)",
        {"time_tag": "delivery", "stall": "1", "stall_at": "2.2", "stall_for": "1.5"},
    ),
    ("Fill gaps with estimates", {"rate_policy": "extrapolate"}),
]

GUIDELINES = {
    "G1": "No unique system has a direct link to the baseline. Everything goes through the Bridge.",
    "G2": "Data is translated only where it needs to be. If both sides already agree, it passes through untouched.",
    "G3": "Every datum keeps the time it was true at, not the time it was delivered, so receivers can always tell how old it is.",
    "G4": "The Bridge only reads what the baseline publishes and only sends what the baseline accepts. Anything else is rejected and logged.",
    "G5": "The mapping is compiled from a YAML file into a Python module ahead of time; nothing is looked up at run time.",
    "G6": "The Bridge is one unit, with its parts kept apart inside it.",
}

EXPERIMENTS = {
    "topology": (
        "Topology sweep",
        "How many interfaces does each way of connecting things need as the number of systems grows?",
        False,
    ),
    "change-impact": (
        "Change impact",
        "What does a change to the baseline touch? Includes a random (Monte Carlo) comparison.",
        True,
    ),
    "timing": (
        "Timing and continuity",
        "Stall the Bridge and see how old the data looks, with correct and with delivery-time stamping.",
        False,
    ),
    "containment": (
        "Fault containment",
        "A faulty system sends bad messages. Who has to deal with them?",
        False,
    ),
}

HELP_TERMS = [
    (
        "Baseline Island",
        "The host platform. Someone else built and certified it. You cannot change it, only use what it publishes.",
    ),
    (
        "Unique Island",
        "Your own systems. Each speaks its own language (its own bus, units and rates) and changes on its own schedule.",
    ),
    ("Bridge", "The one layer between the two islands. Every exchange goes through it."),
    (
        "OEM Interface Unit (OIU)",
        "The part of the Bridge facing the baseline. It follows the baseline's published interface exactly and only answers when asked.",
    ),
    (
        "Translator Module (TM)",
        "Does all the converting: units, formats, names, and the handling of time.",
    ),
    (
        "Unique Interface Distributor (UID)",
        "Keeps a separate connection to each of your systems, in that system's own language.",
    ),
    ("Health monitor", "Notices stale data and stalls, and tells the systems affected."),
    ("Cyber monitor", "Decides which data may cross the Bridge and in which direction."),
    (
        "Point to point (P2P)",
        "The alternative: every system is wired straight to the baseline and carries its own translation.",
    ),
    ("Age of the data", "How long ago the value was true when it arrived. Low is good."),
    ("Late", "Arrived older than the receiving system's own latency budget allows."),
    (
        "Estimate",
        "A predicted value used to fill a gap when a system wants data faster than the baseline sends it. Marked as an estimate; never presented as a measurement.",
    ),
    (
        "Stall",
        "The Bridge stops working for a while, then resumes. A test of how well problems are noticed.",
    ),
]


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def _one_decimal(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _first(form: Mapping[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key)
    return values[0].strip() if values else default


def _num(
    form: Mapping[str, list[str]], key: str, default: float, low: float, high: float, label: str
) -> float:
    raw = _first(form, key)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ScenarioError(f"{label} must be a number, you entered {raw!r}") from None
    if not low <= value <= high:
        raise ScenarioError(f"{label} must be between {low:g} and {high:g}, you entered {value:g}")
    return value


class App:
    """The whole app, with no sockets: ``handle`` takes a request and returns a response."""

    def __init__(self, port: int = 0) -> None:
        self.token = secrets.token_urlsafe(16)
        self.port = port
        self._results: OrderedDict[str, dict[str, tuple[str, bytes]]] = OrderedDict()
        self._lock = threading.Lock()

    # ---- plumbing ------------------------------------------------------------------------

    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _store(self, files: dict[str, tuple[str, bytes]]) -> str:
        run_id = secrets.token_urlsafe(9)
        self._results[run_id] = files
        while len(self._results) > KEEP_RESULTS:
            self._results.popitem(last=False)
        return run_id

    def handle(
        self, method: str, target: str, headers: Mapping[str, str], body: bytes = b""
    ) -> Response:
        if headers.get("Host", "") not in self._allowed_hosts():
            return self._plain(403, "Unexpected Host header. Open the address printed by the app.")
        url = urlsplit(target)
        query = parse_qs(url.query, keep_blank_values=True)
        try:
            if method == "GET":
                return self._get(url.path, query)
            if method == "POST":
                if len(body) > MAX_BODY:
                    return self._plain(413, "That request is too large.")
                form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
                if not hmac.compare_digest(_first(form, "t"), self.token):
                    return self._plain(
                        403, "This page has expired. Go back, reload it and try again."
                    )
                return self._post(url.path, form)
        except ScenarioError as exc:
            return self._page("Something to fix", self._error_html(str(exc)), "/", status=200)
        except FileNotFoundError as exc:
            message = (
                "The example files were not found. Run the app from a clone of the repository, "
                f"or set BRIDGE_ISLAND_EXAMPLES. ({exc})"
            )
            return self._page("Examples not found", self._error_html(message), "/", status=200)
        except Exception:  # last line of defence: one bad request must not take the app down
            traceback.print_exc()
            message = "Something went wrong inside the app. The details are in the black window."
            return self._page("Something went wrong", self._error_html(message), "/", status=500)
        return self._plain(405, "Method not allowed.")

    @staticmethod
    def _plain(status: int, text: str) -> Response:
        return Response(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    @staticmethod
    def _error_html(message: str) -> str:
        return (
            f'<div class="error" role="alert"><strong>Please check this:</strong> {E(message)}</div>'
            '<p>Use your browser\'s Back button to change it, or <a href="/">start again</a>.</p>'
        )

    def _page(self, title: str, body: str, active: str, status: int = 200) -> Response:
        nav = "".join(
            f'<a href="{href}"{" aria-current=page" if href == active else ""}>{E(label)}</a>'
            for href, label in NAV
        )
        doc = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{E(title)} - bridge-island</title><style>{REPORT_CSS}{EXTRA_CSS}</style></head><body>"
            '<a class="skip" href="#main">Skip to content</a><main id="main">'
            f'<header class="top"><strong>bridge-island {bridge_island.__version__}</strong>'
            f'<span class="muted">{E(NOTE)}</span></header><nav aria-label="Main">{nav}</nav>{body}'
            "</main></body></html>"
        )
        return Response(status, doc.encode("utf-8"), headers={})

    def _hidden(self) -> str:
        return f'<input type="hidden" name="t" value="{self.token}">'

    # ---- routing -------------------------------------------------------------------------

    def _get(self, path: str, query: Mapping[str, list[str]]) -> Response:
        if path == "/":
            return self._page("Run a simulation", self._home(query), "/")
        if path == "/scenarios":
            return self._page("Scenario files", self._scenarios_page(), "/scenarios")
        if path == "/report":
            return self._page("Full report", self._report_form(query), "/report")
        if path == "/rules":
            return self._page("Check the guidelines", self._rules_form(), "/rules")
        if path == "/experiments":
            return self._page("Experiments", self._experiments_form(), "/experiments")
        if path == "/help":
            return self._page("Help", self._help(), "/help")
        if path.startswith("/download/"):
            return self._download(path)
        return self._page(
            "Not found", '<h1>Page not found</h1><p><a href="/">Back to the start</a></p>', "", 404
        )

    def _post(self, path: str, form: Mapping[str, list[str]]) -> Response:
        if path == "/run":
            return self._run_quick(form)
        if path == "/run-scenario":
            return self._run_scenario_form(form)
        if path == "/report":
            return self._report(form)
        if path == "/rules":
            return self._rules(form)
        if path == "/experiments":
            return self._experiment(form)
        return self._plain(404, "Not found.")

    def _download(self, path: str) -> Response:
        parts = unquote(path).split("/")
        if len(parts) != 4 or parts[1] != "download":
            return self._plain(404, "Not found.")
        files = self._results.get(parts[2])
        entry = files.get(parts[3]) if files else None
        if entry is None:
            return self._plain(404, "That result is no longer available. Run it again.")
        content_type, data = entry
        return Response(
            200, data, content_type, {"Content-Disposition": f'attachment; filename="{parts[3]}"'}
        )

    # ---- forms ---------------------------------------------------------------------------

    def _example_select(self, selected: str, element_id: str = "example") -> str:
        options = "".join(
            f'<option value="{k}"{" selected" if k == selected else ""}>{E(k)}: '
            f"{E((load_world(k).__doc__ or '').splitlines()[0].split(':')[0])}</option>"
            for k in EXAMPLES
        )
        return f'<select id="{element_id}" name="example">{options}</select>'

    @staticmethod
    def _select(name: str, options: list[tuple[str, str]], selected: str) -> str:
        opts = "".join(
            f'<option value="{E(v)}"{" selected" if v == selected else ""}>{E(label)}</option>'
            for v, label in options
        )
        return f'<select id="{name}" name="{name}">{opts}</select>'

    def _home(self, q: Mapping[str, list[str]], error: str = "") -> str:
        def v(k: str, d: str = "") -> str:
            return _first(q, k, d)

        faults = [("none", "None")] + [
            (k, label) for k, (label, _, _) in fault_messages(load_world("aviation"), 1).items()
        ]
        presets = "".join(
            f'<a href="/?{"&".join(f"{k}={E(val)}" for k, val in {"example": v("example", "aviation"), **p}.items())}">{E(name)}</a>'
            for name, p in PRESETS
        )
        form = f"""
<form class="card" method="post" action="/run">{self._hidden()}
<h2>Run a simulation</h2>
<p>Pick an example, choose what should happen during the run, and press Run. The result explains in plain
sentences what happened. Nothing is saved on your computer unless you download it.</p>
<div class="presets" role="group" aria-label="Quick starts"><strong>Quick starts:</strong> {presets}</div>
<div class="row">
 <div><label for="example">Example</label>{self._example_select(v("example", "aviation"))}
  <p class="hint">Two invented platforms: an air vehicle and an infusion device.</p></div>
 <div><label for="version">Baseline version</label>{self._select("version", [("1", "Version 1"), ("2", "Version 2 (a change from version 1)")], v("version", "1"))}
  <p class="hint">Version 2 renames, converts and removes some elements.</p></div>
</div>
<div class="row">
 <div><label for="duration">How long (seconds of simulated time)</label><input id="duration" name="duration" type="number" min="1" max="{MAX_DURATION_S:g}" step="any" value="{E(v("duration", "8"))}"></div>
 <div><label for="seed">Random seed</label><input id="seed" name="seed" type="number" step="1" value="{E(v("seed", "1"))}">
  <p class="hint">The same seed always gives the same result.</p></div>
</div>
<fieldset><legend>How the Bridge behaves</legend>
<div class="row">
 <div><label for="time_tag">Time stamping</label>{self._select("time_tag", [("source", "Source time (correct)"), ("delivery", "Delivery time (the error the design forbids)")], v("time_tag", "source"))}
  <p class="hint">With delivery time, old data looks new.</p></div>
 <div><label for="rate_policy">When a system wants data faster than the baseline sends it</label>{self._select("rate_policy", [("cap", "Only pass real data (cap)"), ("extrapolate", "Fill gaps with marked estimates")], v("rate_policy", "cap"))}
  <p class="hint">Neither gives the system everything it asked for.</p></div>
</div></fieldset>
<fieldset><legend>Things that go wrong (optional)</legend>
<label class="check"><input type="checkbox" name="stall" value="1"{" checked" if v("stall") else ""}> Stall the Bridge for a while</label>
<div class="row"><div><label for="stall_at">Stall starts at (s)</label><input id="stall_at" name="stall_at" type="number" min="0" step="any" value="{E(v("stall_at", "2.2"))}"></div>
<div><label for="stall_for">Stall lasts (s)</label><input id="stall_for" name="stall_for" type="number" min="0.1" step="any" value="{E(v("stall_for", "1.5"))}"></div></div>
<div class="row"><div><label for="fault">A faulty system sends</label>{self._select("fault", faults, v("fault", "none"))}
<p class="hint">The faulty system is the one that can send requests to the baseline.</p></div>
<div><label for="fault_at">at (s)</label><input id="fault_at" name="fault_at" type="number" min="0" step="any" value="{E(v("fault_at", "2"))}"></div></div>
</fieldset>
<button type="submit">Run</button>
</form>"""
        return f"<h1>bridge-island</h1>{error}{form}"

    def _scenario_dict(self, form: Mapping[str, list[str]]) -> dict[str, Any]:
        example = _first(form, "example", "aviation")
        if example not in EXAMPLES:
            raise ScenarioError(f"Choose one of the examples: {', '.join(EXAMPLES)}")
        world = load_world(example)
        version = int(_num(form, "version", world.VERSIONS[0], 1, 99, "Baseline version"))
        duration = _num(form, "duration", 8.0, 1.0, MAX_DURATION_S, "How long")
        seed = int(_num(form, "seed", 1, -(10**9), 10**9, "Random seed"))
        events: list[dict[str, Any]] = []
        if _first(form, "stall"):
            at = _num(form, "stall_at", 2.2, 0, duration, "Stall start")
            events.append(
                {"at": at, "stall": _num(form, "stall_for", 1.5, 0.1, duration, "Stall length")}
            )
        fault = _first(form, "fault", "none")
        if fault != "none":
            catalogue = fault_messages(
                world, version if version in world.VERSIONS else world.VERSIONS[0]
            )
            if fault not in catalogue:
                raise ScenarioError("Choose one of the listed faulty messages.")
            _, message, values = catalogue[fault]
            events.append(
                {
                    "at": _num(form, "fault_at", 2.0, 0, duration, "Faulty message time"),
                    "send": {
                        "from": world.CONTAINMENT["sender"],
                        "message": message,
                        "values": values,
                    },
                }
            )
        data: dict[str, Any] = {
            "name": "from_the_app",
            "example": example,
            "version": version,
            "seed": seed,
            "duration_s": duration,
            "bridge": {
                "time_tag": _first(form, "time_tag", "source"),
                "rate_policy": _first(form, "rate_policy", "cap"),
            },
        }
        if events:
            data["events"] = events
        return data

    # ---- running -------------------------------------------------------------------------

    def _simulate(self, data: Any, source: str) -> tuple[Scenario, System, ModuleType, int]:
        scenario = (
            parse_scenario_text(data, source)
            if isinstance(data, str)
            else parse_scenario(data, source)
        )
        world = load_world(scenario.example)
        with self._lock:
            system, version = run_scenario(scenario, world)
        return scenario, system, world, version

    def _results_html(
        self,
        scenario: Scenario,
        system: System,
        world: ModuleType,
        version: int,
        scenario_yaml: str,
        form_html: str = "",
    ) -> str:
        summary = summarize(system)
        trace = trace_rows(system)
        man = manifest(scenario, world, version)
        run_id = self._store(
            {
                "summary.json": ("application/json", json.dumps(summary, indent=2).encode()),
                "manifest.json": ("application/json", json.dumps(man, indent=2).encode()),
                "trace.csv": ("text/csv; charset=utf-8", trace_csv_text(trace).encode()),
                "scenario.yaml": ("text/yaml; charset=utf-8", scenario_yaml.encode()),
            }
        )
        rows = []
        for name, s in summary["subsystems"].items():
            rows.append(
                [
                    name,
                    s["protocol"],
                    s["received"],
                    s["estimated"],
                    _one_decimal(s["mean_age_ms"]),
                    _one_decimal(s["time_avg_age_ms"]),
                    _one_decimal(s["peak_age_ms"]),
                    s["late"],
                    "; ".join(s["issues"]) or "none",
                ]
            )
        ev = summary["bridge"]["events"]
        ev_rows = [[f"{e['time_s']:g}", e["kind"], e["detail"]] for e in ev[:40]]
        more = f" (first 40 of {len(ev)})" if len(ev) > 40 else ""
        found = "".join(f"<li>{E(t)}</li>" for t in findings(summary))
        downloads = "".join(
            f'<a class="button secondary" href="/download/{run_id}/{name}">{E(label)}</a>'
            for name, label in [
                ("summary.json", "Summary (JSON)"),
                ("trace.csv", "Every datum (CSV)"),
                ("scenario.yaml", "These settings as a scenario file"),
                ("manifest.json", "Reproducibility record"),
            ]
        )
        return (
            f"<h1>Result: {E(scenario.example)}, baseline version {version}</h1>"
            f'<p class="banner">{E(NOTE)} {scenario.duration_s:g} s simulated, seed {scenario.seed}.</p>'
            f'<div class="card"><h2>What happened</h2><ul class="found">{found}</ul></div>'
            "<h2>What each system received</h2>"
            + table(
                "Counts, and how old the data was: when it arrived, on average over time, and at worst",
                [
                    "System",
                    "Native bus",
                    "Received",
                    "Estimated",
                    "Age at arrival (ms)",
                    "Average age (ms)",
                    "Peak age (ms)",
                    "Late",
                    "Problems",
                ],
                rows,
                numeric={2, 3, 4, 5, 6, 7},
            )
            + "<h2>Age of the data over time</h2>"
            + age_charts("app", list(summary["subsystems"]), trace, scenario.duration_s)
            + (
                table(
                    f"What the Bridge logged{more}",
                    ["Time (s)", "Kind", "Detail"],
                    ev_rows,
                    numeric={0},
                )
                if ev_rows
                else "<p>The Bridge logged nothing unusual.</p>"
            )
            + f"<h2>Keep the results</h2><p>{downloads}</p>"
            + form_html
        )

    def _run_quick(self, form: Mapping[str, list[str]]) -> Response:
        data = self._scenario_dict(form)
        scenario, system, world, version = self._simulate(data, "the form")
        yaml_text = yaml.safe_dump(data, sort_keys=False)
        again = "<h2>Change something and run again</h2>" + self._home(form).split("</h1>", 1)[1]
        body = self._results_html(scenario, system, world, version, yaml_text, again)
        return self._page("Result", body, "/")

    # ---- scenario files ------------------------------------------------------------------

    def _shipped(self) -> dict[str, tuple[str, str]]:
        out = {}
        try:
            for p in sorted((examples_dir() / "scenarios").glob("*.yaml")):
                text = p.read_text(encoding="utf-8")
                data = load_yaml(text) or {}
                out[p.name] = (str(data.get("description", "")), text)
        except (FileNotFoundError, YamlError):
            pass
        return out

    def _scenarios_page(self) -> str:
        shipped = self._shipped()
        options = "".join(f'<option value="{E(n)}">{E(n)}</option>' for n in shipped)
        details = "".join(
            f"<details><summary>{E(n)}</summary><p>{E(d)}</p><pre>{E(t)}</pre></details>"
            for n, (d, t) in shipped.items()
        )
        sample = next(iter(shipped.values()), ("", ""))[1]
        return f"""<h1>Scenario files</h1>
<p>A scenario file describes one run completely, so that it can be shared and repeated exactly. Run one of the
shipped files, or paste your own.</p>
<form class="card" method="post" action="/run-scenario">{self._hidden()}
<h2>Run a shipped scenario</h2><label for="name">Scenario</label><select id="name" name="name">{options}</select>
<p><button type="submit">Run this scenario</button></p></form>
<form class="card" method="post" action="/run-scenario">{self._hidden()}
<h2>Or paste your own</h2><p class="hint">The file format is explained on the Help page. You can also load a file from your computer into the box.</p>
<label for="file">Load a file into the box</label><input id="file" type="file" accept=".yaml,.yml,.txt">
<label for="yaml">Scenario text</label><textarea id="yaml" name="yaml" spellcheck="false">{E(sample)}</textarea>
<p><button type="submit">Run the pasted scenario</button></p></form>
<h2>The shipped files</h2>{details}
<script>document.getElementById('file').addEventListener('change',function(e){{var f=e.target.files[0];if(!f)return;
var r=new FileReader();r.onload=function(){{document.getElementById('yaml').value=r.result}};r.readAsText(f)}});</script>"""  # noqa: S608  (HTML text, not a query)

    def _run_scenario_form(self, form: Mapping[str, list[str]]) -> Response:
        text = form.get("yaml", [""])[0]
        if not text.strip():
            shipped = self._shipped()
            name = _first(form, "name")
            if name not in shipped:  # only names from the shipped list are accepted, never a path
                raise ScenarioError("Choose a scenario from the list.")
            text = shipped[name][1]
        scenario, system, world, version = self._simulate(text, "the scenario")
        header = f"<p>{E(scenario.description)}</p>" if scenario.description else ""
        body = header + self._results_html(scenario, system, world, version, text)
        return self._page("Result", body, "/scenarios")

    # ---- report --------------------------------------------------------------------------

    def _report_form(self, q: Mapping[str, list[str]]) -> str:
        def v(k: str, d: str = "") -> str:
            return _first(q, k, d)

        return f"""<h1>Full report</h1>
<p>One page with everything for an example: the architecture, all six guidelines on both baseline versions, what each
system received, the mapping, and what the change between versions touches. You can open it here or download it as a
single file that works offline.</p>
<form class="card" method="post" action="/report">{self._hidden()}
<div class="row"><div><label for="example">Example</label>{self._example_select(v("example", "aviation"))}</div>
<div><label for="duration">How long (s)</label><input id="duration" name="duration" type="number" min="1" max="{MAX_DURATION_S:g}" step="any" value="{E(v("duration", "6"))}"></div>
<div><label for="seed">Random seed</label><input id="seed" name="seed" type="number" step="1" value="{E(v("seed", "1"))}"></div></div>
<div class="row"><div><label for="rate_policy">Rate policy</label>{self._select("rate_policy", [("cap", "Only pass real data (cap)"), ("extrapolate", "Fill gaps with marked estimates")], v("rate_policy", "cap"))}</div></div>
<label class="check"><input type="checkbox" name="stall" value="1"> Stall the Bridge during the run</label>
<div class="row"><div><label for="stall_at">Stall starts at (s)</label><input id="stall_at" name="stall_at" type="number" min="0" step="any" value="2.2"></div>
<div><label for="stall_for">Stall lasts (s)</label><input id="stall_for" name="stall_for" type="number" min="0.1" step="any" value="1.5"></div></div>
<button type="submit" name="mode" value="open">Open the report</button>
<button type="submit" name="mode" value="download" class="secondary">Download as a file</button>
</form>"""

    def _report(self, form: Mapping[str, list[str]]) -> Response:
        example = _first(form, "example", "aviation")
        if example not in EXAMPLES:
            raise ScenarioError(f"Choose one of the examples: {', '.join(EXAMPLES)}")
        duration = _num(form, "duration", 6.0, 1.0, 30.0, "How long")
        seed = int(_num(form, "seed", 1, -(10**9), 10**9, "Random seed"))
        policy = _first(form, "rate_policy", "cap")
        if policy not in ("cap", "extrapolate"):
            raise ScenarioError("Choose a rate policy from the list.")
        stall = None
        if _first(form, "stall"):
            stall = (
                _num(form, "stall_at", 2.2, 0, duration, "Stall start"),
                _num(form, "stall_for", 1.5, 0.1, duration, "Stall length"),
            )
        with self._lock:
            page = build_report(load_world(example), seed, policy, duration, stall)
        if _first(form, "mode") == "download":
            return Response(
                200,
                page.encode(),
                headers={
                    "Content-Disposition": f'attachment; filename="bridge-island-{example}.html"'
                },
            )
        bar = '<p><a href="/report">&larr; Back to bridge-island</a></p>'
        return Response(200, page.replace('<main id="main">', f'<main id="main">{bar}', 1).encode())

    # ---- guidelines ----------------------------------------------------------------------

    def _rules_form(self) -> str:
        explain = "".join(f"<dt>{k}</dt><dd>{E(v)}</dd>" for k, v in GUIDELINES.items())
        return f"""<h1>Check the guidelines</h1>
<p>The design rests on six guidelines. This runs a test for each one on an example and says pass or fail.</p>
<form class="card" method="post" action="/rules">{self._hidden()}
<div class="row"><div><label for="example">Example</label>{self._example_select("aviation")}</div>
<div><label for="version">Baseline version</label>{self._select("version", [("1", "Version 1"), ("2", "Version 2")], "1")}</div></div>
<button type="submit">Check the six guidelines</button></form>
<h2>What the guidelines say</h2><dl>{explain}</dl>"""

    def _rules(self, form: Mapping[str, list[str]]) -> Response:
        example = _first(form, "example", "aviation")
        if example not in EXAMPLES:
            raise ScenarioError(f"Choose one of the examples: {', '.join(EXAMPLES)}")
        world = load_world(example)
        version = int(_num(form, "version", world.VERSIONS[0], 1, 99, "Baseline version"))
        if version not in world.VERSIONS:
            raise ScenarioError(
                f"The {example} example has baseline versions {list(world.VERSIONS)}."
            )
        with self._lock:
            results = check_rules(world, version)
        rows = [
            [
                r.rule,
                r.title,
                f'<span class="{"pass" if r.passed else "fail"}">{"&#10003; PASS" if r.passed else "&#10007; FAIL"}</span>',
                E(r.detail),
            ]
            for r in results
        ]
        ok = sum(r.passed for r in results)
        body = (
            f"<h1>Guidelines: {E(example)}, baseline version {version}</h1>"
            f'<p class="banner"><strong>{ok} of {len(results)} pass.</strong> {E(NOTE)}</p>'
            + table(
                "Each guideline and what the test found",
                ["Rule", "Guideline", "Result", "What was found"],
                rows,
                raw={2, 3},
            )
            + '<p><a class="button secondary" href="/rules">Check another</a></p>'
        )
        return self._page("Guidelines", body, "/rules")

    # ---- experiments ---------------------------------------------------------------------

    def _experiments_form(self) -> str:
        cards = []
        for key, (title, text, has_trials) in EXPERIMENTS.items():
            trials = (
                f'<label for="trials-{key}">Random trials</label><input id="trials-{key}" name="trials" type="number" min="10" max="{MAX_TRIALS}" value="300">'
                if has_trials
                else ""
            )
            cards.append(
                f"""<form class="card" method="post" action="/experiments">{self._hidden()}
<h2>{E(title)}</h2><p>{E(text)}</p><input type="hidden" name="name" value="{key}">
<div class="row"><div><label for="ex-{key}">Example</label>{self._example_select("aviation", f"ex-{key}")}</div>
<div><label for="seed-{key}">Random seed</label><input id="seed-{key}" name="seed" type="number" value="1"></div><div>{trials}</div></div>
<button type="submit">Run</button></form>"""
            )
        return (
            "<h1>Experiments</h1><p>Four repeatable experiments. Each gives a table you can read and files you can download.</p>"
            + "".join(cards)
        )

    def _experiment(self, form: Mapping[str, list[str]]) -> Response:
        name = _first(form, "name")
        if name not in EXPERIMENTS:
            raise ScenarioError("Choose one of the experiments.")
        example = _first(form, "example", "aviation")
        if example not in EXAMPLES:
            raise ScenarioError(f"Choose one of the examples: {', '.join(EXAMPLES)}")
        seed = int(_num(form, "seed", 1, -(10**9), 10**9, "Random seed"))
        trials = int(_num(form, "trials", 300, 10, MAX_TRIALS, "Random trials"))
        buffer = io.StringIO()
        with self._lock, tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buffer):
            files = experiments.run(name, load_world(example), seed, Path(tmp), trials)
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
        run_id = self._store(stored)
        links = "".join(
            f'<a class="button secondary" href="/download/{run_id}/{E(n)}">{E(n)}</a>'
            for n in stored
        )
        text = "\n".join(
            line for line in buffer.getvalue().splitlines() if not line.startswith("wrote ")
        )
        title = EXPERIMENTS[name][0]
        body = (
            f'<h1>{E(title)}: {E(example)}</h1><p class="banner">{E(NOTE)} Seed {seed}.</p>'
            f"<pre>{E(text)}</pre>{''.join(images)}<h2>Files</h2><p>{links}</p>"
            '<p><a class="button secondary" href="/experiments">Run another</a></p>'
        )
        return self._page(title, body, "/experiments")

    # ---- help ----------------------------------------------------------------------------

    def _help(self) -> str:
        terms = "".join(f"<dt>{E(t)}</dt><dd>{E(d)}</dd>" for t, d in HELP_TERMS)
        return f"""<h1>Help</h1>
<h2>What is this?</h2>
<p>A simulation that shows an integration pattern: a host platform that you cannot change (the Baseline Island), your own
systems (the Unique Island), and one layer between them (the Bridge). You can change what happens during a run and see what the
Bridge does about it. {E(NOTE)} It is not flight or medical software and is not certified.</p>
<h2>Where do I start?</h2>
<ol><li>On <a href="/">Run a simulation</a>, press <em>Run</em> with the defaults, then try a quick start such as
<em>The Bridge stalls for 1.5 s</em>.</li>
<li>Read <em>What happened</em> at the top of the result.</li>
<li>Try <a href="/rules">Check the guidelines</a> and the <a href="/report">Full report</a>.</li></ol>
<h2>Reading the charts</h2>
<p>Each chart shows one system. Across: when the data was delivered. Up: how old the data was when it arrived. A dot far
above the others is old data. Different systems use different marker shapes, and hollow markers are estimates, so colour is
never the only clue.</p>
<h2>Scenario files</h2>
<p>A scenario is a short text file in YAML. Example:</p>
<pre>name: my_run
example: aviation       # aviation or medical
version: 1              # baseline version
seed: 1
duration_s: 8
bridge:
  time_tag: source      # source (correct) or delivery (the error)
  rate_policy: cap      # cap or extrapolate
events:
  - {{at: 2.2, stall: 1.5}}</pre>
<p>On the result page, <em>These settings as a scenario file</em> downloads the file for the run you just made.</p>
<h2>Words used</h2><dl>{terms}</dl>
<h2>Stopping the app</h2><p>Close the black window that opened with it, or press Ctrl+C in it. It only runs on your own computer and
nothing is sent anywhere.</p>"""


# ---- the server --------------------------------------------------------------------------


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "bridge-island"
        timeout = 15  # seconds: a client that stalls cannot hold a thread for ever

        def _send(self, resp: Response) -> None:
            # Only our own page has a script, written as a bare <script>; user text is escaped.
            # Each response gets a fresh nonce, and a page without a script allows none.
            body, script_src = resp.body, "'none'"
            if b"<script>" in body:
                nonce = secrets.token_urlsafe(16)
                body = body.replace(b"<script>", f'<script nonce="{nonce}">'.encode(), 1)
                script_src = f"'nonce-{nonce}'"
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'none'; style-src 'unsafe-inline'; script-src {script_src}; "
                "img-src data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            for key, value in resp.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self, method: str, body: bytes = b"") -> None:
            self._send(app.handle(method, self.path, dict(self.headers.items()), body))

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._send(App._plain(400, "Bad request: the Content-Length is not valid."))
                return
            if length > MAX_BODY:  # refuse at once: do not wait for a body we would not accept
                self._send(App._plain(413, "That request is too large."))
                return
            self._dispatch("POST", self.rfile.read(length))

        def log_message(self, format: str, *args: Any) -> None:
            pass  # keep the window quiet

    return Handler


class _Server(ThreadingHTTPServer):
    """No address reuse: on Windows, reuse lets a second copy bind the same port as the first
    and silently share it. With it off, a busy port is reported and the next one is tried."""

    allow_reuse_address = False
    daemon_threads = True


def make_server(port: int = 8765) -> tuple[ThreadingHTTPServer, App]:
    """Bind to 127.0.0.1 only, trying the next ports if the first is busy."""
    last: OSError | None = None
    for candidate in range(port, port + 20) if port else [0]:
        app = App(candidate)
        try:
            server = _Server(("127.0.0.1", candidate), make_handler(app))
        except OSError as exc:
            last = exc
            continue
        app.port = server.server_address[1]
        return server, app
    raise OSError(f"no free port from {port} to {port + 19}: {last}")


def _say(message: str) -> None:
    print(message, flush=True)


def serve(port: int = 8765, open_browser: bool = True, out: Callable[[str], None] = _say) -> None:
    server, app = make_server(port)
    url = f"http://127.0.0.1:{app.port}/"
    out(f"bridge-island is running at {url}")
    out("Leave this window open while you use it. Press Ctrl+C here, or close the window, to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("\nStopped.")
    finally:
        server.server_close()
