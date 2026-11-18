"""The "Tour": the architecture, the data flow and the faults, drawn for a first-time reader.

One inline SVG per figure, in two flavours: the generic one (``/tour``, any platform) and the
insulin loop's worked example (``/insulin/tour``). Moving parts are CSS animations and the pickers
are radio buttons, so no script is needed (the app's policy only allows its own nonce'd script) and
``prefers-reduced-motion`` is honoured. A fault is shown by colour, a dashed line and a cross, never
colour alone. Synthetic, illustrative; not medical software.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from bridge_island.clinical.views import EXPLAIN

E = html.escape

# lane id, x0, x1, y: the six message lanes, the same in every drawing
GEOMETRY = [
    (1, 630, 500, 50),
    (2, 300, 170, 125),
    (3, 170, 300, 175),
    (4, 500, 630, 150),
    (5, 630, 500, 200),
    (6, 500, 630, 280),
]
NONE = "No fault", "Nothing is wrong. Every message arrives. Pick a fault to see where it breaks."


@dataclass(frozen=True)
class Fault:
    label: str
    href: str  # a page that runs it
    text: str
    cut: tuple[int, ...] = ()  # lanes where messages stop
    ghost: tuple[int, ...] = ()  # lanes that carry a message that cannot be trusted
    stall: bool = False  # the Bridge itself stops


@dataclass(frozen=True)
class Drawing:
    host: tuple[str, str, str]
    devices: tuple[tuple[str, str], tuple[str, str], tuple[str, str]]  # top, middle, bottom
    lanes: tuple[str, ...]  # a short label per lane
    names: tuple[tuple[str, str], ...]  # (from, to) per lane
    steps: tuple[str, ...]  # one sentence per lane
    faults: tuple[Fault, ...]


GENERIC = Drawing(
    host=("Baseline platform", "Baseline Island", "cannot be changed"),
    devices=(
        ("System A", "Unique Island"),
        ("System B", "Unique Island"),
        ("System C", "Unique Island"),
    ),
    lanes=("reading", "translated", "published", "translated", "reply", "status"),
    names=(
        ("System A", "Bridge"),
        ("Bridge", "baseline"),
        ("baseline", "Bridge"),
        ("Bridge", "System B"),
        ("System B", "Bridge"),
        ("Bridge", "System C"),
    ),
    steps=(
        "System A sends a reading in its own format, at its own rate.",
        "The Bridge translates it into the format the baseline already understands, and keeps the time the reading was true.",
        "The baseline (which nobody can change) publishes a message, as it always has.",
        "The Bridge translates it into System B's own format and rate where it needs translating; where both sides agree it passes through untouched.",
        "System B answers. The Bridge turns the answer into something the baseline accepts.",
        "The Bridge tells System C what it needs to know, and the health monitor watches that the data stays fresh.",
    ),
    faults=(
        Fault(
            "The Bridge stalls",
            "/?stall=1&stall_at=2.2&stall_for=1.5",
            "The Bridge itself stops for a moment (the quick start stalls it for 1.5 s). Nothing passes in either direction. This is its single point of failure; the health monitor notices stale data and tells the systems affected.",
            cut=(1, 2, 3, 4, 5, 6),
            stall=True,
        ),
        Fault(
            "The baseline is updated",
            "/?version=2",
            "The platform you cannot change is updated to version 2: some elements are renamed, converted or removed. The mapping is compiled from a file ahead of time, so you can see which path from the baseline to a system is now removed.",
            cut=(4,),
        ),
        Fault(
            "A system sends a bad message",
            "/?fault=out_of_range&fault_at=2",
            "A system sends a value that is out of range. The Bridge only passes on what the baseline accepts, so the message is rejected and logged and never reaches the baseline.",
            cut=(2,),
            ghost=(1,),
        ),
        Fault(
            "Data stamped with arrival time",
            "/?time_tag=delivery&stall=1&stall_at=2.2&stall_for=1.5",
            "The error to avoid: stamping data with when it arrived instead of when it was true. After a stall, old data then looks fresh and receivers cannot tell how old it is. The red dots look new but are not.",
            ghost=(2, 4),
        ),
    ),
)

INSULIN = Drawing(
    host=("Controller host", "Baseline Island", "cannot be changed"),
    devices=(
        ("Glucose sensor", "Unique Island"),
        ("Insulin pump", "Unique Island"),
        ("Phone display", "Unique Island"),
    ),
    lanes=("reading", "glucose", "dose command", "dose", "confirmation", "status, alarms"),
    names=(
        ("Sensor", "Bridge"),
        ("Bridge", "Controller host"),
        ("Controller host", "Bridge"),
        ("Bridge", "Pump"),
        ("Pump", "Bridge"),
        ("Bridge", "Phone display"),
    ),
    steps=(
        "The sensor sends a glucose reading in its own maker's format.",
        "The Bridge translates it into the format the controller host already understands, and keeps the time it was measured.",
        "The controller host (which nobody can change) decides a dose and publishes a command.",
        "The Bridge translates the command into the pump's own format, for example units into 0.05 U pulses.",
        "The pump says what it delivered. The Bridge turns that into a confirmation the host accepts.",
        "The Bridge tells the phone display the loop's state, and any alarm. If the display does not acknowledge, that is logged.",
    ),
    faults=(
        Fault(
            "Sensor goes quiet",
            "/insulin?scenario=sensor_dropout",
            EXPLAIN["sensor_dropout"],
            cut=(1,),
        ),
        Fault(
            "Pump stops confirming",
            "/insulin?scenario=pump_silent",
            EXPLAIN["pump_silent"],
            cut=(5,),
        ),
        Fault(
            "Old command replayed",
            "/insulin?scenario=replayed_command",
            EXPLAIN["replayed_command"],
            ghost=(4,),
        ),
        Fault(
            "Phone holds the app back",
            "/insulin?scenario=display_throttle",
            EXPLAIN["display_throttle"],
            cut=(6,),
        ),
        Fault(
            "The Bridge stalls",
            "/insulin?scenario=bridge_stall",
            EXPLAIN["bridge_stall"],
            cut=(1, 2, 3, 4, 5, 6),
            stall=True,
        ),
    ),
)

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
.tour{--bad:#b91c1c}@media (prefers-color-scheme:dark){.tour{--bad:#f87171}}
.tour .stall{fill:none;stroke:var(--bad);stroke-width:3;stroke-dasharray:6 4}
.tour .rp{fill:var(--bad);opacity:0}
.tour .still .p{opacity:1}
@media (prefers-reduced-motion:reduce){.tour .p,.tour .rp{animation:none!important;opacity:1}}
"""


def _anim() -> str:
    """Keyframes per lane: ``c`` loops all six in order, ``s`` runs one alone, ``x`` stops half way."""
    out = []
    for i, x0, x1, _ in GEOMETRY:
        dx, a = x1 - x0, (i - 1) * 100 / 6
        b = a + 14
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


def _picker_css(prefix: str, n: int) -> str:
    """The checked label and its caption, for ``n`` choices."""
    return "".join(
        f".tour #{prefix}{k}:checked~.pick label[for={prefix}{k}]{{background:var(--brand);color:var(--btn-fg);border-color:var(--brand)}}"
        f".tour #{prefix}{k}:checked~.cap.c{prefix}{k}{{display:block}}"
        for k in range(n)
    )


def _flow_css(prefix: str) -> str:
    """Step ``i`` (``{prefix}{i}``; 0 is "play all") shows lane ``i`` alone and dims the rest."""
    return "".join(
        f".tour #{prefix}{i}:checked~.stage .p{{animation:none;opacity:0}}"
        f".tour #{prefix}{i}:checked~.stage .p{i}{{animation:s{i} 2.6s linear infinite;opacity:1}}"
        f".tour #{prefix}{i}:checked~.stage .lane:not(.l{i}){{opacity:.25}}"
        for i, *_ in GEOMETRY
    )


def _fault_css(prefix: str, faults: tuple[Fault, ...]) -> str:
    """Fault ``k`` (``{prefix}{k}``; 0 is "no fault") cuts its lanes, adds bad dots, outlines the Bridge."""
    out = []
    for k, f in enumerate(faults, start=1):
        sel = f".tour #{prefix}{k}:checked~.stage"
        for i in f.cut:
            out.append(
                f"{sel} .p{i}{{animation:x{i} 4s linear infinite}}{sel} .cut{i}{{display:inline}}"
                f"{sel} .l{i}{{stroke:var(--bad);stroke-dasharray:5 4}}"
            )
        for i in f.ghost:
            out.append(f"{sel} .rp{i}{{display:inline;animation:s{i} 3s linear infinite}}")
        if f.stall:
            out.append(f"{sel} .stall{{display:inline}}")
    return "".join(out)


def _svg(prefix: str, d: Drawing, mode: str) -> str:
    """``mode``: ``still`` (no dots), ``flow`` (dots) or ``fault`` (dots, crosses and bad dots)."""
    lanes, dots = [], []
    for (i, x0, x1, y), label, (a, b) in zip(GEOMETRY, d.lanes, d.names, strict=True):
        tx = (x0 + x1) / 2
        tip = x1 - 6 if x1 > x0 else x1 + 6
        lanes.append(
            f'<g><line class="lane l{i}" marker-end="url(#arr{prefix})" x1="{x0}" y1="{y}" x2="{tip}" y2="{y}"/>'
            f'<text x="{tx}" y="{y - 7}" text-anchor="middle" font-size="11">{E(label)}</text>'
            f"<title>{E(a)} to {E(b)}: {E(label)}</title></g>"
        )
        if mode != "still":
            dots.append(f'<circle class="p p{i}" cx="{x0}" cy="{y}" r="6"/>')
        if mode == "fault":
            dots.append(f'<circle class="rp rp{i}" cx="{x0}" cy="{y}" r="6"/>')
            dots.append(
                f'<text class="cut cut{i}" x="{tx}" y="{y + 6}" text-anchor="middle" font-size="22" font-weight="700" style="fill:var(--bad)">&#215;</text>'
            )
    (h1, h2, h3), ((t1, t2), (p1, p2), (s1, s2)) = d.host, d.devices
    return f"""<svg viewBox="0 0 800 330" role="img" aria-label="The baseline platform on the left, the Bridge in the middle and three systems on the right, with arrows for each message">
<defs><marker id="arr{prefix}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>
<rect class="host" x="10" y="100" width="160" height="130" rx="10"/><text class="b" x="90" y="150" text-anchor="middle">{E(h1)}</text><text x="90" y="170" text-anchor="middle" font-size="11">{E(h2)}</text><text x="90" y="188" text-anchor="middle" font-size="11">{E(h3)}</text>
<rect class="box" x="300" y="10" width="200" height="310" rx="12"/><text class="b" x="400" y="38" text-anchor="middle">The Bridge</text>
<text x="400" y="100" text-anchor="middle" font-size="12">OIU: talks to the baseline</text><text x="400" y="165" text-anchor="middle" font-size="12">Translator: the compiled mapping</text><text x="400" y="230" text-anchor="middle" font-size="12">UID: talks to the systems</text>
<rect class="stall" x="296" y="6" width="208" height="318" rx="14"/>
<rect class="host" x="630" y="20" width="160" height="60" rx="10"/><text class="b" x="710" y="46" text-anchor="middle">{E(t1)}</text><text x="710" y="64" text-anchor="middle" font-size="11">{E(t2)}</text>
<rect class="host" x="630" y="120" width="160" height="110" rx="10"/><text class="b" x="710" y="168" text-anchor="middle">{E(p1)}</text><text x="710" y="186" text-anchor="middle" font-size="11">{E(p2)}</text>
<rect class="host" x="630" y="250" width="160" height="60" rx="10"/><text class="b" x="710" y="276" text-anchor="middle">{E(s1)}</text><text x="710" y="294" text-anchor="middle" font-size="11">{E(s2)}</text>
{"".join(lanes)}{"".join(dots)}</svg>"""


def _figure(prefix: str, d: Drawing, items: list[tuple[str, str]], mode: str) -> str:
    """Radios first (so CSS can reach what follows them), then the buttons, the drawing and the captions."""
    radios = "".join(
        f'<input type="radio" id="{prefix}{k}" name="{prefix}"{" checked" if k == 0 else ""}>'
        for k in range(len(items))
    )
    labels = "".join(f'<label for="{prefix}{k}">{E(t)}</label>' for k, (t, _) in enumerate(items))
    caps = "".join(
        f'<p class="cap c{prefix}{k}" aria-live="polite">{E(c)}</p>'
        for k, (_, c) in enumerate(items)
    )
    return (
        f'<div class="tour">{radios}<div class="pick" role="group" aria-label="Choose">{labels}</div>'
        f'<div class="stage">{_svg(prefix, d, mode)}</div>{caps}</div>'
    )


def _page(d: Drawing, tag: str, intro: str, islands: str, look: str, more: str) -> str:
    flow = [
        ("Play all", "All six messages in order, over and over. Pick a step to watch one alone.")
    ]
    flow += [
        (f"{n}. {a} to {b}", t)
        for n, ((a, b), t) in enumerate(zip(d.names, d.steps, strict=True), 1)
    ]
    faults = [NONE] + [(f.label, f.text) for f in d.faults]
    runs = "".join(f'<a href="{E(f.href)}">{E(f.label)}</a>' for f in d.faults)
    s_id, f_id = f"st{tag}", f"ft{tag}"
    css = (
        CSS
        + _anim()
        + _picker_css(s_id, len(flow))
        + _picker_css(f_id, len(faults))
        + _flow_css(s_id)
        + _fault_css(f_id, d.faults)
    )
    return f"""<style>{css}</style>{intro}
<h2>1. The architecture: three islands</h2>{islands}
<div class="tour"><div class="stage still">{_svg(tag + "a", d, "still")}</div></div>
<dl><dt>Baseline Island</dt><dd>The platform nobody can change, and what it publishes and accepts. Read-only to you.</dd>
<dt>The Bridge</dt><dd>Three parts in one unit: the OIU speaks to the baseline, the Translator applies a mapping compiled ahead of time from a YAML file, and the UID speaks to the systems. A health monitor and a cyber monitor sit alongside.</dd>
<dt>Unique Island</dt><dd>The systems you connect, each with its own protocol, units and rate.</dd></dl>
<h2>2. The data flow: one trip around the loop</h2>
<p>Pick a step, or watch all six. The dot is a message; the arrow shows which way it goes.</p>
{_figure(s_id, d, flow, "flow")}
<h2>3. The faults: where it can break</h2>
<p>Choose a fault. A red dashed line with a cross is where messages stop; a red dot is a message that cannot be trusted. The text says what it means in the simulation.</p>
{_figure(f_id, d, faults, "fault")}
<h2>4. What to look for in a result</h2>{look}
<h2>Try it</h2>
<div class="presets" role="group" aria-label="Run a fault from the tour"><strong>Run:</strong> {runs} {more}</div>"""


def tour_page() -> str:
    """The generic tour, for any platform."""
    return _page(
        GENERIC,
        "g",
        '<p class="lede">A five-minute picture book of the idea behind this app. Nothing here is saved or sent anywhere; the animations only show the idea. '
        'Prefer a worked example? See it <a href="/insulin/tour">drawn for the insulin loop</a>.</p>',
        "<p>Think of three islands. On the left is the <strong>baseline platform</strong>: someone else built and certified it, so it is fixed. On the right are "
        "<strong>your systems</strong>, each speaking its own language. In the middle is <strong>the Bridge</strong>, the only thing you build: it translates in both "
        "directions, so neither side has to change.</p>",
        "<ol><li><strong>Who had to change?</strong> With the Bridge, only the Bridge. Compare it with wiring every system straight to the baseline (point to point), where each carries its own translation.</li>"
        "<li><strong>How old is the data?</strong> Every datum keeps the time it was true, so a receiver can always tell how stale it is, even after a stall.</li>"
        "<li><strong>Who dealt with the bad message?</strong> The Bridge should stop it at the boundary and log it. The Bridge is also a risk: when it stalls, everything behind it waits.</li></ol>",
        '<a href="/">Choose your own</a>',
    )


def insulin_tour_page() -> str:
    """The same tour, drawn for the insulin loop's devices."""
    return _page(
        INSULIN,
        "i",
        '<p class="lede">The <a href="/tour">general tour</a> drawn for the insulin loop: a controller host that cannot change, a sensor, a pump and a phone display from other makers, and the Bridge between them.</p>',
        "<p>The controller host is the fixed island, the three devices are the unique island, and the Bridge translates between them.</p>",
        "<ol><li><strong>Was anyone told?</strong> The timeline separates <em>told</em> (the controller or display was informed) from <em>logged</em> (only written down). A fault that is logged but never told is the dangerous kind.</li>"
        "<li><strong>Time in range.</strong> The glucose chart's shaded band is 70 to 180. The three ways of connecting the devices share the same patient, meals and faults, so any difference is down to the connection.</li>"
        "<li><strong>The Bridge is also a risk.</strong> Try <em>The Bridge stalls</em>: direct does better there, because it has no Bridge to stall.</li></ol>",
        '<a href="/insulin">Choose your own</a>',
    )
