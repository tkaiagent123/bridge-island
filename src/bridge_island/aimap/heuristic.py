"""The non-AI baseline: match by name, infer the conversion from the representations.

For every input the old mapping fed: keep the same element if the new interface still publishes it;
otherwise take the most similar element name (same message preferred); otherwise mark the entry
removed. The conversion is inferred from the source and target representations (a binary angle is
decoded, differing units are converted), keeping any range check the old entry had. It cannot
recode an enumeration, because the interface does not say how the codes line up, so a proposal
that needs one is rejected by the contract check. Uplinks are copied unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any

from bridge_island.aimap.controls import reply_text
from bridge_island.aimap.parse import downlink_id
from bridge_island.aimap.task import Task
from bridge_island.core.elements import Representation

SIMILAR = 0.75  # name similarity needed to treat two elements as the same thing renamed


def infer_steps(
    src: Representation,
    dst: Representation,
    old: Sequence[Mapping[str, Any]] = (),
    keep_enums: bool = False,
) -> list[dict[str, Any]]:
    """Steps taking ``src`` to ``dst``, keeping the old chain's range checks.

    The old chain's enumerations are kept only when ``keep_enums`` says the source is unchanged.
    """
    steps: list[dict[str, Any]] = []
    rep = src
    if rep.encoding.startswith("bam") and rep.encoding[3:].isdigit() and dst.encoding == "float64":
        steps.append({"bam_decode": int(rep.encoding[3:])})
        rep = replace(rep, encoding="float64", units="deg")
    elif rep.encoding == "float64" and rep.units == "deg" and dst.encoding.startswith("bam"):
        steps.append({"bam_encode": int(dst.encoding[3:])})
        rep = replace(rep, encoding=dst.encoding)
    if rep.units != dst.units:
        steps.append({"unit": [rep.units, dst.units]})
    ranges = [dict(s) for s in old if isinstance(s, dict) and "valid_range" in s]
    enums = [dict(s) for s in old if isinstance(s, dict) and "enum" in s]
    if enums and keep_enums:
        steps += enums  # the source did not change, so the old recoding still applies
    first_is_range = bool(old) and isinstance(old[0], dict) and "valid_range" in old[0]
    return ranges + steps if first_is_range else steps + ranges


def _best_match(task: Task, ref: str) -> tuple[str | None, str]:
    """The new element for an old one: ``(ref, "exact" | "similar")`` or ``(None, "")``."""
    if task.v2.element(ref) is not None:
        return ref, "exact"
    message, _, name = ref.partition(".")
    best, best_score = None, SIMILAR
    for msg in task.v2.published:
        for e in msg.elements:
            ratio = SequenceMatcher(None, name, e.name).ratio() + (
                0.05 if msg.name == message else 0
            )
            if ratio > best_score:
                best, best_score = f"{msg.name}.{e.name}", ratio
    return best, "similar" if best else ""


def heuristic(task: Task) -> str:
    downlinks: list[dict[str, Any]] = []
    confidence: dict[str, tuple[str, str]] = {}
    for old in task.v1_spec.get("downlinks") or []:
        sub, _, native = str(old["to"]).partition(".")
        need = next(n for n in task.needs[sub] if n.native_name == native)
        ref, how = _best_match(task, str(old["from"]))
        element = task.v2.element(ref) if ref else None
        if ref is None or element is None:
            entry: dict[str, Any] = {
                "from": old["from"],
                "to": old["to"],
                "status": "removed",
                "reason": "no similar element in the new interface",
            }
            level, why = "medium", "no element with a similar name"
        else:
            entry = {"from": ref, "to": old["to"]}
            before = task.v1.element(str(old["from"]))
            unchanged = before is not None and before.representation.same_value_form(
                element.representation
            )
            steps = infer_steps(
                element.representation, need.representation, old.get("transforms") or [], unchanged
            )
            if steps:
                entry["transforms"] = steps
            level, why = (
                ("high", "same element") if how == "exact" else ("medium", f"similar name: {ref}")
            )
        downlinks.append(entry)
        confidence[downlink_id(entry)] = (level, why)
    spec: dict[str, Any] = {
        "name": "heuristic",
        "baseline_version": 2,
        "downlinks": downlinks,
        "uplinks": task.v1_spec.get("uplinks") or [],
    }
    return reply_text(spec, confidence)
