"""The prompt a model receives, built from the task and from the code that defines the format.

The vocabulary section is generated from the transform builders and the units table, so it cannot
list a transform the generator does not accept. It never includes the answer: ``Task.truth``, the
example's rename table and the change-set notes at the top of its ``world.py`` are not read here.
"""

from __future__ import annotations

import json
from typing import Any

from bridge_island.aimap.task import Task, spec_text
from bridge_island.baseline.platform import BaselineInterface
from bridge_island.core.elements import MessageSpec, Representation
from bridge_island.core.transforms import _BUILDERS
from bridge_island.core.units import _UNITS

# One line for each transform, with an example that the tests parse with ``from_spec``.
TRANSFORMS: dict[str, tuple[str, str]] = {
    "unit": ("{unit: [ft, m]}", "convert between two units of the same kind"),
    "scale_offset": (
        '{scale_offset: {scale: 0.5, offset: 1.0, to_units: "1"}}',
        "value * scale + offset; to_units names the resulting unit",
    ),
    "enum": (
        "{enum: {map: {MAP: 1, STATUS: 2}, to_encoding: uint8}}",
        "replace named or numeric codes by others; to_encoding is the resulting encoding",
    ),
    "bam_decode": ("{bam_decode: 16}", "binary angle with this many bits to float degrees"),
    "bam_encode": ("{bam_encode: 16}", "float degrees to a binary angle with this many bits"),
    "bit_unpack": ("{bit_unpack: [[low, 4], [high, 4]]}", "split an integer into named bit fields"),
    "bit_pack": ("{bit_pack: [[low, 4], [high, 4]]}", "combine named bit fields into an integer"),
    "valid_range": (
        "{valid_range: [0, 3]}",
        "reject values outside the range; it does not change the value",
    ),
    "quantize": (
        "{quantize: {scale: 20, encoding: uint16, units: pulse}}",
        "multiply, round to a whole number and carry it as this integer encoding and unit",
    ),
}

FORMAT = """\
name: <any text>
baseline_version: 2
downlinks:            # baseline element -> one input of one unique system
  - {from: Message.element, to: System.input, transforms: [<transform>, ...]}
  - from: Message.element          # an element the new baseline no longer publishes
    to: System.input
    status: removed
    reason: <one line>
uplinks:              # a message a unique system sends -> a message the baseline accepts
  - from: System.output_message
    to: BaselineMessage
    fields:
      - {from: output_field, to: baseline_field, transforms: [<transform>, ...]}
"""

RULES = """\
- Every input a system declares must be mapped exactly once.
- Translate only where the representation differs (encoding, units or frame). If the element
  already matches what the system needs, leave out `transforms`, except a range check that the
  old mapping already had.
- If the element a system needs is no longer published, mark that entry `status: removed` with a
  one-line `reason`. Do not guess a substitute.
- Keep the old mapping's range checks (`valid_range`) unless the new interface changes what
  they mean.
- Elements the new interface adds, and that no system declares, need no entry.
- Every field of a system's output message must be mapped exactly once, and every field of the
  baseline message it feeds must be filled exactly once.
"""

REPLY = """\
Reply with one YAML document and nothing else. It has two keys: `mapping`, the complete new mapping
in the format above, and `confidence`, a list with one item for every downlink and every uplink
field. Each item names its entry as "<from> -> <to>" (for an uplink field:
"System.output_message.field -> BaselineMessage.field"), says `high`, `medium` or `low`, and gives
a one-line reason. Use `low` or `medium` for anything you are unsure about.

Shape:
mapping:
  name: ...
  baseline_version: 2
  downlinks: [...]
  uplinks: [...]
confidence:
  - {entry: "Message.element -> System.input", confidence: high, reason: "same unit and encoding"}
"""


def vocabulary() -> str:
    """The transforms and units the generator accepts, from the code that defines them."""
    missing = set(_BUILDERS) - set(TRANSFORMS)
    if missing:
        raise RuntimeError(f"transforms without a description: {sorted(missing)}")
    lines = [f"- `{example}`: {text}" for key, (example, text) in TRANSFORMS.items()]
    kinds: dict[str, list[str]] = {}
    for unit, (dimension, _, _) in _UNITS.items():
        kinds.setdefault(dimension, []).append(unit)
    units = "; ".join(f"{dim}: {', '.join(names)}" for dim, names in kinds.items())
    return "\n".join(lines) + f"\n\nUnits known to `unit` (convertible within a kind): {units}"


def _rep(rep: Representation) -> dict[str, str]:
    return {"encoding": rep.encoding, "units": rep.units, "frame": rep.frame}


def _message(msg: MessageSpec) -> dict[str, Any]:
    elements = []
    for e in msg.elements:
        item: dict[str, Any] = {"name": e.name, **_rep(e.representation)}
        if e.valid_range is not None:
            item["range"] = list(e.valid_range)
        elements.append(item)
    return {"message": msg.name, "rate_hz": msg.rate_hz, "elements": elements}


def interface_json(interface: BaselineInterface) -> str:
    data = {
        "version": interface.version,
        "published": [_message(m) for m in interface.published],
        "accepted": [_message(m) for m in interface.accepted],
    }
    return json.dumps(data, indent=1)


def systems_json(task: Task) -> str:
    data: dict[str, Any] = {}
    for sub, needs in task.needs.items():
        data[sub] = {
            "inputs": [
                {"name": n.native_name, **_rep(n.representation), "max_rate_hz": n.rate_hz}
                for n in needs
            ],
            "outputs": [
                {"message": o.message_name, "fields": [{"name": n, **_rep(r)} for n, r in o.fields]}
                for o in task.outputs.get(sub, ())
            ],
        }
    return json.dumps(data, indent=1)


def v1_mapping_text(task: Task) -> str:
    """The old mapping as data: comments and the world file name are not part of the task."""
    spec = {k: v for k, v in task.v1_spec.items() if k != "world"}
    return spec_text(spec)


def build_prompt(task: Task) -> str:
    notes = "".join(f"- {ref}: {text}\n" for ref, text in task.notes)
    notes_section = f"\n## Notes on the new interface\n{notes}" if notes else ""
    return f"""\
You maintain the mapping layer between a host platform's published interface and the independent
systems attached to it. The host platform has changed its interface from version 1 to version 2.
Write the mapping for version 2. All names and numbers are invented.

## Baseline interface, version 1
{interface_json(task.v1)}

## Baseline interface, version 2
{interface_json(task.v2)}
{notes_section}
## The systems: what each one needs and sends (unchanged)
{systems_json(task)}

## Mapping for version 1
{v1_mapping_text(task)}
## Mapping file format
{FORMAT}
## Transforms you may use
{vocabulary()}

## Rules
{RULES}
## Reply format
{REPLY}"""
