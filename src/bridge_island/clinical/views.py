"""HTML pieces for the insulin loop, shared by the offline report and the app.

All content is invented and every number synthetic, illustrative. Not medical software, not a
validated patient model or dosing algorithm, not for clinical use.
"""

from __future__ import annotations

import functools
import html
from collections.abc import Sequence
from typing import Any

from bridge_island.bridge.contract import ContractSpec
from bridge_island.clinical import envelope as Env
from bridge_island.clinical import loop as L
from bridge_island.clinical import metrics as M
from bridge_island.clinical import svg
from bridge_island.clinical.experiment import summarize

E = html.escape
DISCLAIMER = (
    "Synthetic, illustrative. Not medical software; not a validated patient model or dosing "
    "algorithm; not for clinical use."
)
SECTION_TITLES = {
    "owner": "1. Owner",
    "regulatory_home": "1. Regulatory home",
    "data_items": "2. Data items",
    "timing": "3. Timing",
    "failure_behavior": "4. Failure behaviour",
    "security": "5. Security",
    "claims": "6. Claims",
    "change_envelope": "7. Change envelope",
    "residual": "8. Residual: what the Bridge cannot assure",
}
EXPLAIN = {
    "nominal": "Nothing goes wrong. The three architectures should look the same, and they do: this is the baseline every other result is compared with.",
    "sensor_swap_units": "At 08:00 the glucose sensor is replaced by a model from another maker that reports mmol/L every minute. With direct integration the host's old adapter cannot read it, so the controller works from its last reading until its own 30-minute watchdog falls back to the programmed basal. Through the Bridge, one mapping entry converts the units, inside the declared change envelope.",
    "sensor_dropout": "The sensor sends nothing for 45 minutes after lunch. The question is how long the controller keeps acting on its last reading before someone says the data is too old.",
    "stale_burst": "Readings are held back in a relay and then arrive in bursts, still carrying their old sample times. Each reading looks normal; only its age is wrong.",
    "pump_fw_v2": "The pump moves to firmware v2: the bolus is now sent as 0.05 U pulses and one field is renamed. An adapter that was not updated sends units where the pump now expects pulses, and nothing complains.",
    "pump_silent": "The pump stops confirming commands for an hour; for the second half it also stops delivering.",
    "out_of_range": "The sensor reports 600 mg/dL marked OK, then error codes. A range check catches the value; the question is what the controller is told.",
    "replayed_command": "An old dose command, still inside its valid-until time, is put back on the path to the pump. A pump that does not check command ids gives the bolus again.",
    "display_throttle": "The phone's operating system holds the display app back for two hours while glucose goes low. Alarms arrive late or not at all.",
    "bridge_stall": "The Bridge itself stops for 20 minutes. This is the single point of failure: direct has no Bridge to stall, so here direct does better.",
}


def badge(passed: bool | None) -> str:
    if passed is None:
        return '<span class="muted">not run</span>'
    return (
        '<span class="pass">&#10003; pass</span>'
        if passed
        else '<span class="fail">&#10007; fail</span>'
    )


@functools.lru_cache(maxsize=1)
def claim_results() -> dict[str, bool]:
    """Each claim's conformance test on the default devices (the "last run" the card shows)."""
    return {
        name.split("/")[1]: problem is None
        for name, problem in Env.verify([f"claim/C{i}" for i in range(1, 7)])
    }


def contract_card(spec: ContractSpec | None = None, results: dict[str, bool] | None = None) -> str:
    spec = spec or L.contract_spec()
    raw = spec.raw
    parts = [
        f'<p class="banner">{E(DISCLAIMER)} The contract is an engineering illustration, not a regulatory document.</p>'
    ]
    parts.append(f"<h3>{SECTION_TITLES['owner']}</h3><p>{E(spec.owner)}</p>")
    parts.append(f"<h3>{SECTION_TITLES['regulatory_home']}</h3><p>{E(spec.regulatory_home)}</p>")
    rows = "".join(
        f"<tr><td><code>{E(i.name)}</code></td><td>{E(i.units)}</td><td class='n'>{i.lo:g} to {i.hi:g}</td>"
        f"<td>{E(i.encoding)}</td><td>{E(i.role or '')}</td></tr>"
        for i in spec.items
    )
    parts.append(
        f"<h3>{SECTION_TITLES['data_items']}</h3><table><caption>Every item that crosses the Bridge</caption>"
        f"<tr><th>Item</th><th>Units</th><th>Range</th><th>Encoding</th><th>Role</th></tr>{rows}</table>"
    )
    ages = ", ".join(f"{k}: {v:g} s" for k, v in spec.max_age_s.items())
    parts.append(
        f"<h3>{SECTION_TITLES['timing']}</h3><ul><li>Maximum data age, from the source time: {E(ages)}</li>"
        f"<li>Flagged {spec.flag_margin_s:g} s before that (the controller reads up to a check period and a frame later)</li>"
        f"<li>End-to-end latency budget: {spec.latency_budget_s:g} s</li><li>Checked every {spec.check_period_s:g} s</li></ul>"
    )
    parts.append(
        f"<h3>{SECTION_TITLES['failure_behavior']}</h3><ul>"
        "<li>Stale, missing or out-of-range glucose: the controller gets a reading flagged FAILSAFE, every command "
        "carries the fail-safe basal (the installed patient's programmed basal) until valid glucose reaches the "
        "controller again, and an alarm goes to the display.</li>"
        f"<li>A command not confirmed within {spec.confirm_timeout_s:g} s is re-sent at most {spec.confirm_max_resends} "
        "times, never with its bolus; then it is alarmed and the controller is told.</li>"
        f"<li>An alarm the {E(spec.alarm_target)} does not acknowledge within {spec.alarm_ack_timeout_s:g} s is re-sent at "
        f"most {spec.alarm_max_resends} times, then logged as undelivered with its age.</li></ul>"
    )
    sources = "; ".join(f"{k} from {', '.join(sorted(v))}" for k, v in spec.sources.items())
    parts.append(
        f"<h3>{SECTION_TITLES['security']}</h3><ul><li>Toward the devices: {E(', '.join(sorted(spec.down)))}</li>"
        f"<li>Toward the host: {E(sources)}</li><li>Replay protection: by command id and valid-until time</li></ul>"
    )
    results = results if results is not None else {}
    claims = "".join(
        f"<tr><td>{E(c.id)}</td><td>{E(c.text)}</td><td>{'<br>'.join(f'<code>{E(t)}</code>' for t in c.tests)}</td>"
        f"<td>{badge(results.get(c.id))}</td></tr>"
        for c in spec.claims
    )
    parts.append(
        f"<h3>{SECTION_TITLES['claims']}</h3><table><caption>Each claim, the test that checks it, and its result "
        f"in the last conformance run</caption><tr><th>Id</th><th>Claim</th><th>Test</th><th>Last run</th></tr>{claims}</table>"
    )
    env = "".join(
        f"<li><code>{E(k)}</code>: {E(str(v))}</li>" for k, v in raw["change_envelope"].items()
    )
    parts.append(f"<h3>{SECTION_TITLES['change_envelope']}</h3><ul>{env}</ul>")
    parts.append(
        f"<h3>{SECTION_TITLES['residual']}</h3><ul>{''.join(f'<li>{E(r)}</li>' for r in spec.residual)}</ul>"
    )
    return "".join(parts)


def _table(caption: str, head: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{E(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><caption>{E(caption)}</caption><tr>{''.join(f'<th>{E(h)}</th>' for h in head)}</tr>{body}</table>"


def _minutes(seconds: Any) -> str:
    return "not told" if seconds is None else f"{seconds / 60:.0f} min"


def single_run(
    scenario_name: str, patient: int, archs: Sequence[str], seed: int = 1
) -> dict[str, Any]:
    from bridge_island.clinical import scenarios as S

    scenario = S.by_name(scenario_name)
    loops = {}
    for arch in archs:
        lp = L.build_loop(arch, L.patients(seed)[patient], scenario)
        lp.run()
        loops[arch] = lp
    return {
        "scenario": scenario,
        "loops": loops,
        "rows": {a: M.measure(lp) for a, lp in loops.items()},
    }


def run_view(scenario_name: str, patient: int, archs: Sequence[str], seed: int = 1) -> str:
    from bridge_island.clinical.figures import failsafe_spans

    data = single_run(scenario_name, patient, archs, seed)
    scenario, loops, rows = data["scenario"], data["loops"], data["rows"]
    first = next(iter(loops.values()))
    events = [(s, k.replace("_", " ")) for k, s, _ in L.fault_windows(scenario, first.meals)]
    shaded = [span for lp in loops.values() for span in failsafe_spans(lp)]
    tiles = "".join(
        f'<div class="tile"><div class="k">{E(a)}</div><div class="v">{r["tir_70_180_min"]} min</div>'
        f'<div class="k">in 70-180 mg/dL; below 54: {r["below_54_min"]} min; told: {_minutes(r["time_to_detect_s"]) if r["detected"] is not None else "no fault"}; '
        f"unflagged stale glucose used: {r['unsafe_age_min']:g} min</div></div>"
        for a, r in rows.items()
    )
    deliveries: dict[str, Sequence[tuple[float, float]]] = {
        a: [
            (t, b)
            for n in lp.devices
            if n.startswith("Pump")
            for t, _, b, _ in lp.pump(n).executed
            if b > 0
        ]
        for a, lp in loops.items()
    }
    timeline = []
    for arch, lp in loops.items():
        for t, by, what in M.told_events(lp)[:8]:
            timeline.append((f"{t / 3600:05.2f} h", arch, "told", by, what[:110]))
        for t, by, what in M.detections(lp)[:6]:
            timeline.append((f"{t / 3600:05.2f} h", arch, "logged", by, what[:110]))
    timeline.sort()
    pid = L.patients(seed)[patient].id
    return (
        f'<p class="banner">{E(DISCLAIMER)} Virtual patient {E(pid)}, seed {seed}, 24 h simulated.</p>'
        f"<h2>What happened</h2><p>{E(EXPLAIN.get(scenario.name, scenario.description))}</p>"
        f"<ul class='found'>{''.join(f'<li>{E(s)}</li>' for s in explain_rows(scenario.name, rows))}</ul>"
        f'<div class="tiles">{tiles}</div>'
        f"<h2>Glucose</h2><p>{svg.legend(list(loops))}</p>"
        + svg.glucose_chart(
            "g",
            {a: lp.patient.trace for a, lp in loops.items()},
            events,
            shaded,
            f"{scenario.name}, {pid}",
        )
        + "<h2>Insulin delivered</h2>"
        + svg.insulin_strip("ins", deliveries)
        + "<h2>Event timeline</h2>"
        + (
            _table(
                "Faults found: told (the controller or display was told) and logged (written down only)",
                ["Time", "Architecture", "", "By", "What"],
                timeline[:60],
            )
            if timeline
            else "<p>No fault reports.</p>"
        )
    )


def explain_rows(name: str, rows: dict[str, dict[str, Any]]) -> list[str]:
    """Plain sentences, each with its count, comparing the architectures that ran."""
    out = []
    for arch, r in rows.items():
        bits = [f"{r['tir_70_180_min']} of 1440 minutes in 70-180 mg/dL"]
        if r["below_54_min"]:
            bits.append(f"{r['below_54_min']} minutes below 54")
        if r["detected"] is not None:
            bits.append(
                "the controller or display was told after " + _minutes(r["time_to_detect_s"])
                if r["detected"]
                else "nobody was told"
            )
        if r["unsafe_age_min"]:
            bits.append(
                f"the controller acted on unflagged stale glucose for {r['unsafe_age_min']:g} minutes"
            )
        if r["unit_errors_to_pump"] or r["unit_errors_to_controller"]:
            bits.append(
                f"{r['unit_errors_to_pump'] + r['unit_errors_to_controller']} unit or encoding errors got through"
            )
        if abs(r["delivered_minus_commanded_u"]) > 0.5:
            bits.append(
                f"{r['delivered_minus_commanded_u']:+.1f} U delivered compared with what was commanded"
            )
        if r["alarms_late"] or r["alarms_lost"]:
            bits.append(
                f"{r['alarms_late']} alarms reached the display late and {r['alarms_lost']} never did"
            )
        if r["paths_failed_in_stall"]:
            bits.append(
                f"{r['paths_failed_in_stall']} of 3 device paths carried nothing during the stall"
            )
        out.append(f"{arch}: " + "; ".join(bits) + ".")
    return out


def cohort_view(
    scenario_name: str, archs: Sequence[str], seed: int = 1, workers: int | None = None
) -> str:
    from bridge_island.clinical.experiment import run_matrix

    rows, _ = run_matrix([scenario_name], archs, range(30), seed, workers)
    summary = summarize(rows)
    table = _table(
        "30 virtual patients: median [5th, 95th percentile] or count",
        [
            "Architecture",
            "Minutes in 70-180",
            "Patients below 54",
            "Told",
            "Patients using unflagged stale glucose",
            "Unit errors through",
        ],
        [
            (
                s["architecture"],
                f"{s['tir_70_180_min_median']:g} [{s['tir_70_180_min_p5']:g}, {s['tir_70_180_min_p95']:g}]",
                s["patients_below_54"],
                "no fault" if s["detected"] is None else f"{s['detected']} of 30",
                s["patients_unsafe_age"],
                s["unit_errors_to_controller_total"] + s["unit_errors_to_pump_total"],
            )
            for s in summary
        ],
    )
    return (
        f'<p class="banner">{E(DISCLAIMER)} 30 virtual patients, seed {seed}.</p>'
        f"<h2>What happened</h2><p>{E(EXPLAIN.get(scenario_name, ''))}</p>{table}"
    )


def ledger_view(change_stem: str) -> str:
    change = next((c for c in Env.shipped_changes() if c.path and c.path.stem == change_stem), None)
    if change is None:
        raise KeyError(change_stem)
    d = Env.check(change)
    verdict = (
        '<p class="banner"><strong>Inside the envelope.</strong> The Bridge absorbs it: the mapping changes and the '
        f"{len(d.tests)} conformance tests below are re-run. The controller host is not touched.</p>"
        if d.inside
        else '<p class="banner"><strong>Outside the envelope.</strong> The Bridge cannot absorb it alone:</p>'
        + "<ul>"
        + "".join(f"<li>{E(r)}</li>" for r in d.reasons)
        + "</ul>"
    )
    owners = "".join(f"<li><strong>{E(o)}</strong>: {E(w)}</li>" for o, w in d.owners)
    tests = "".join(f"<li><code>{E(t)}</code></li>" for t in d.tests)
    declares = "".join(
        f"<li><code>{E(k)}</code>: {E(str(v))}</li>" for k, v in change.declares.items()
    )
    return (
        f"<h2>{E(change.name)}</h2><p>{E(change.description)}</p><h3>What it declares</h3><ul>{declares}</ul>{verdict}"
        f"<h3>Organizations to involve</h3><ul>{owners}</ul>"
        + (f"<h3>Conformance tests to re-run</h3><ul>{tests}</ul>" if tests else "")
        + f'<p class="muted">{E(DISCLAIMER)} This is an engineering check, not a regulatory determination.</p>'
    )
