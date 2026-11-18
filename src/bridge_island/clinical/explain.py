"""The "Tour" page: the architecture, the data flow and the faults, drawn for a first-time reader.

One inline SVG, drawn three times (still, stepped, with a fault). Moving parts are CSS animations
and the pickers are radio buttons, so no script is needed (the app's policy only allows its own
nonce'd script) and ``prefers-reduced-motion`` is honoured. A fault is shown by colour, a dashed
line and a cross, never colour alone. Synthetic, illustrative; not medical software.
"""

from __future__ import annotations

import html

from bridge_island.clinical.views import EXPLAIN

E = html.escape

# id, from, to, x0, x1, y, label
LANES = [
    (1, "Sensor", "Bridge", 630, 500, 50, "reading"),
    (2, "Bridge", "Controller host", 300, 170, 125, "glucose"),
    (3, "Controller host", "Bridge", 170, 300, 175, "dose command"),
    (4, "Bridge", "Pump", 500, 630, 150, "dose"),
    (5, "Pump", "Bridge", 630, 500, 200, "confirmation"),
    (6, "Bridge", "Phone display", 500, 630, 280, "status, alarms"),
]
STEPS = [
    "The sensor sends a glucose reading in its own maker's format.",
    "The Bridge translates it into the format the controller host already understands, and keeps the time it was measured.",
    "The controller host (which nobody can change) decides a dose and publishes a command.",
    "The Bridge translates the command into the pump's own format, for example units into 0.05 U pulses.",
    "The pump says what it delivered. The Bridge turns that into a confirmation the host accepts.",
    "The Bridge tells the phone display the loop's state, and any alarm. If the display does not acknowledge, that is logged.",
]
# fault -> (label, scenario to run, lanes cut, text). Text is the shipped explanation.
FAULTS = {
    "sensor_dropout": ("Sensor goes quiet", "sensor_dropout", (1,)),
    "pump_silent": ("Pump stops confirming", "pump_silent", (5,)),
    "replayed_command": ("Old command replayed", "replayed_command", ()),
    "display_throttle": ("Phone holds the app back", "display_throttle", (6,)),
    "bridge_stall": ("The Bridge itself stalls", "bridge_stall", (1, 2, 3, 4, 5, 6)),
}

CSS = """
.tour{margin:1rem 0}.tour input{position:absolute;opacity:0;pointer-events:none}
.tour .pick{display:flex;flex-wrap:wrap;gap:.4rem;margin:.5rem 0}
.tour .pick label{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:.3rem .8rem;cursor:pointer;font-size:.9rem}
.tour .pick label:hover{border-color:var(--brand)}
.tour svg{width:100%;height:auto;max-width:800px;background:var(--card);border:1px solid var(--line);border-radius:10px;color:var(--muted)}
.tour svg text{fill:currentColor;font:13px system-ui,sans-serif}.tour svg .b{font-weight:700;fill:var(--brand)}
.tour .box{fill:var(--brand-soft);stroke:var(--brand);stroke-width:1.5}
.tour .host{fill:var(--card);stroke:var(--muted);stroke-width:1.5}
.tour .lane{stroke:var(--muted);stroke-width:2;fill:none}
.tour .p{fill:var(--accent);opacity:0}.tour .cut,.tour .rp,.tour .stall{display:none}
.tour .cap{display:none;margin:.6rem 0;padding:.6rem .9rem;border-left:4px solid var(--brand);background:var(--brand-soft)}
.tour .cap.always{display:block}
.tour{--bad:#b91c1c}@media (prefers-color-scheme:dark){.tour{--bad:#f87171}}
.tour .cut{stroke:var(--bad);stroke-width:3}.tour .stall{fill:none;stroke:var(--bad);stroke-width:3;stroke-dasharray:6 4}
.tour .rp{fill:var(--bad)}
.tour input:focus-visible+*{outline:0}
.tour .still .p{opacity:1}
@media (prefers-reduced-motion:reduce){.tour .p{animation:none!important;opacity:1}}
"""


def _anim() -> str:
    """Keyframes per lane: ``c`` loops all six in order, ``s`` runs one alone, ``x`` stops half way."""
    out = []
    for i, _, _, x0, x1, _, _ in LANES:
        dx, a, b = x1 - x0, (i - 1) * 100 / 6, (i - 1) * 100 / 6 + 14
        out.append(
            f"@keyframes c{i}{{0%,{a:.1f}%{{opacity:0;transform:translateX(0)}}{a + 1:.1f}%{{opacity:1}}"
            f"{b - 1:.1f}%{{opacity:1}}{b:.1f}%,100%{{opacity:0;transform:translateX({dx}px)}}}}"
            f"@keyframes s{i}{{0%{{opacity:0;transform:translateX(0)}}10%,85%{{opacity:1}}"
            f"100%{{opacity:0;transform:translateX({dx}px)}}}}"
            f"@keyframes x{i}{{0%{{opacity:0;transform:translateX(0)}}10%{{opacity:1}}"
            f"50%,70%{{opacity:1;transform:translateX({dx / 2}px)}}71%,100%{{opacity:0;transform:translateX({dx / 2}px)}}}}"
            f".tour .p{i}{{animation:c{i} 12s linear infinite}}"
        )
    return "".join(out)


def _picker_css(prefix: str, n_steps: int) -> str:
    """Rules for one picker: the checked label, its caption, and what the diagram does."""
    out = []
    for k in range(n_steps):
        out.append(
            f".tour #{prefix}{k}:checked~.pick label[for={prefix}{k}]{{background:var(--brand);color:var(--btn-fg);border-color:var(--brand)}}"
        )
        out.append(f".tour #{prefix}{k}:checked~.cap.c{prefix}{k}{{display:block}}")
    return "".join(out)


def _fault_css() -> str:
    out = []
    for k, (key, (_, _, lanes)) in enumerate(FAULTS.items(), start=1):
        for i in lanes:
            out.append(f".tour #ft{k}:checked~.stage .p{i}{{animation:x{i} 4s linear infinite}}")
            out.append(f".tour #ft{k}:checked~.stage .cut{i}{{display:inline}}")
            out.append(
                f".tour #ft{k}:checked~.stage .l{i}{{stroke:var(--bad);stroke-dasharray:5 4}}"
            )
        if key == "replayed_command":
            out.append(
                f".tour #ft{k}:checked~.stage .rp{{display:inline;animation:s4 3s linear infinite}}"
            )
        if key == "bridge_stall":
            out.append(f".tour #ft{k}:checked~.stage .stall{{display:inline}}")
    return "".join(out)


def _svg(prefix: str, mode: str) -> str:
    lanes, packets, cuts = [], [], []
    for i, a, b, x0, x1, y, label in LANES:
        tx = (x0 + x1) / 2
        lanes.append(
            f'<g><line class="lane l{i}" marker-end="url(#arr{prefix})" x1="{x0}" y1="{y}" x2="{x1 - (6 if x1 > x0 else -6)}" y2="{y}"/>'
            f'<text x="{tx}" y="{y - 7}" text-anchor="middle" font-size="11">{E(label)}</text>'
            f"<title>{E(a)} to {E(b)}: {E(label)}</title></g>"
        )
        if mode != "still":
            packets.append(f'<circle class="p p{i}" cx="{x0}" cy="{y}" r="6"/>')
            cuts.append(
                f'<text class="cut cut{i}" x="{tx}" y="{y + 6}" text-anchor="middle" font-size="22" font-weight="700" fill="var(--bad)" style="fill:var(--bad)">&#215;</text>'
            )
    replay = '<circle class="rp" cx="500" cy="150" r="6"/>' if mode == "fault" else ""
    return f"""<svg viewBox="0 0 800 330" role="img" aria-label="The controller host on the left, the Bridge in the middle and three devices on the right, with arrows for each message">
<defs><marker id="arr{prefix}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>
<rect class="host" x="10" y="100" width="160" height="130" rx="10"/><text class="b" x="90" y="150" text-anchor="middle">Controller host</text><text x="90" y="170" text-anchor="middle" font-size="11">Baseline Island</text><text x="90" y="188" text-anchor="middle" font-size="11">cannot be changed</text>
<rect class="box" x="300" y="10" width="200" height="310" rx="12"/><text class="b" x="400" y="38" text-anchor="middle">The Bridge</text>
<text x="400" y="100" text-anchor="middle" font-size="12">OIU: talks to the host</text><text x="400" y="165" text-anchor="middle" font-size="12">Translator: the compiled mapping</text><text x="400" y="230" text-anchor="middle" font-size="12">UID: talks to the devices</text>
<rect class="stall" x="296" y="6" width="208" height="318" rx="14"/>
<rect class="host" x="630" y="20" width="160" height="60" rx="10"/><text class="b" x="710" y="46" text-anchor="middle">Glucose sensor</text><text x="710" y="64" text-anchor="middle" font-size="11">Unique Island</text>
<rect class="host" x="630" y="120" width="160" height="110" rx="10"/><text class="b" x="710" y="168" text-anchor="middle">Insulin pump</text><text x="710" y="186" text-anchor="middle" font-size="11">Unique Island</text>
<rect class="host" x="630" y="250" width="160" height="60" rx="10"/><text class="b" x="710" y="276" text-anchor="middle">Phone display</text><text x="710" y="294" text-anchor="middle" font-size="11">Unique Island</text>
{"".join(lanes)}{"".join(packets)}{replay}{"".join(cuts)}</svg>"""


def _picker(prefix: str, name: str, items: list[tuple[str, str]]) -> tuple[str, str, str]:
    """Radio buttons (before the diagram, so CSS can reach it) and one caption per choice."""
    radios = "".join(
        f'<input type="radio" id="{prefix}{k}" name="{name}"{" checked" if k == 0 else ""}>'
        for k in range(len(items))
    )
    labels = "".join(f'<label for="{prefix}{k}">{E(t)}</label>' for k, (t, _) in enumerate(items))
    caps = "".join(
        f'<p class="cap c{prefix}{k}" aria-live="polite">{E(c)}</p>'
        for k, (_, c) in enumerate(items)
    )
    return radios, labels, caps


def _figure(prefix: str, name: str, items: list[tuple[str, str]], mode: str) -> str:
    radios, labels, caps = _picker(prefix, name, items)
    return f'<div class="tour">{radios}<div class="pick" role="group" aria-label="Choose">{labels}</div><div class="stage">{_svg(prefix, mode)}</div>{caps}</div>'


def tour_page() -> str:
    flow_items = [
        ("Play all", "All six messages in order, over and over. Pick a step to watch one alone.")
    ] + [(f"{n}. {LANES[n - 1][1]} to {LANES[n - 1][2]}", t) for n, t in enumerate(STEPS, start=1)]
    fault_items = [
        (
            "No fault",
            "Nothing is wrong. Every message arrives. Pick a fault to see where it breaks.",
        )
    ] + [(label, EXPLAIN[scn]) for label, scn, _ in FAULTS.values()]
    runs = "".join(
        f'<a href="/insulin?scenario={E(s)}">{E(label)}</a>' for label, s, _ in FAULTS.values()
    )
    # "Play all" is st0; step n is st{n}
    flow_css = "".join(
        f".tour #st{i}:checked~.stage .p{{animation:none;opacity:0}}"
        f".tour #st{i}:checked~.stage .p{i}{{animation:s{i} 2.6s linear infinite;opacity:1}}"
        f".tour #st{i}:checked~.stage .lane:not(.l{i}){{opacity:.25}}"
        for i, *_r in LANES
    )
    css = (
        CSS
        + _anim()
        + _picker_css("st", len(flow_items))
        + _picker_css("ft", len(fault_items))
        + flow_css
        + _fault_css()
    )
    return f"""<style>{css}</style>
<p class="lede">A five-minute picture book of the system you are about to run. Nothing here is saved or sent anywhere; the animations only show the idea.</p>
<h2>1. The architecture: three islands</h2>
<p>Think of three islands. On the left is the <strong>controller host</strong>: someone else built and certified it, so it is fixed. On the right are
<strong>devices from other makers</strong>, each speaking its own language. In the middle is <strong>the Bridge</strong>, the only thing you build: it translates in both
directions, so neither side has to change.</p>
<div class="tour"><div class="stage still">{_svg("a", "still")}</div></div>
<dl><dt>Baseline Island</dt><dd>The controller host and what it publishes and accepts. Read-only to you.</dd>
<dt>The Bridge</dt><dd>Three parts in one unit: the OIU speaks to the host, the Translator applies a mapping compiled ahead of time from a YAML file, and the UID speaks to the devices.</dd>
<dt>Unique Island</dt><dd>Sensors, pumps and the phone display, each with its own protocol, units and rate.</dd></dl>
<h2>2. The data flow: one trip around the loop</h2>
<p>Pick a step, or watch all six. The dot is a message; the arrow shows which way it goes.</p>
{_figure("st", "step", flow_items, "flow")}
<h2>3. The faults: where it can break</h2>
<p>Choose a fault. A red dashed line with a cross is where messages stop. The text says what it means in the simulation.</p>
{_figure("ft", "fault", fault_items, "fault")}
<h2>4. What to look for in a result</h2>
<ol><li><strong>Was anyone told?</strong> The timeline separates <em>told</em> (the controller or display was informed) from <em>logged</em> (only written down). A fault that is logged but never told is the dangerous kind.</li>
<li><strong>Time in range.</strong> The glucose chart's shaded band is 70 to 180. The three ways of connecting the devices share the same patient, meals and faults, so any difference is down to the connection.</li>
<li><strong>The Bridge is also a risk.</strong> Try <em>The Bridge itself stalls</em>: direct does better there, because it has no Bridge to stall.</li></ol>
<h2>Try it</h2>
<div class="presets" role="group" aria-label="Run a fault from the tour"><strong>Run:</strong> {runs} <a href="/insulin">Choose your own</a></div>"""
