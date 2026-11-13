"""Turn a model's reply into a mapping spec plus a sidecar of confidences.

The spec stays valid input to ``codegen.generate``: confidences are kept apart from it. A reply that
cannot be parsed is an outcome of the trial, not an error, so nothing here raises on bad text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from bridge_island.yamlutil import YamlError, load_yaml

LEVELS = ("high", "medium", "low")
_FENCE = re.compile(r"```[A-Za-z]*\s*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Parsed:
    spec: dict[str, Any] | None
    confidence: dict[str, tuple[str, str]] = field(default_factory=dict)  # entry id -> (level, why)
    error: str = ""


def downlink_id(entry: Mapping[str, Any]) -> str:
    return f"{entry.get('from')} -> {entry.get('to')}"


def uplink_ids(entry: Mapping[str, Any]) -> list[str]:
    fields = entry.get("fields")
    if not isinstance(fields, list):
        return []
    return [
        f"{entry.get('from')}.{f.get('from')} -> {entry.get('to')}.{f.get('to')}"
        for f in fields
        if isinstance(f, dict)
    ]


def entry_ids(spec: Mapping[str, Any]) -> list[str]:
    """Every entry of a spec that carries a confidence: downlinks, then uplink fields."""
    ids: list[str] = []
    for e in spec.get("downlinks") or []:
        if isinstance(e, dict):
            ids.append(downlink_id(e))
    for e in spec.get("uplinks") or []:
        if isinstance(e, dict):
            ids += uplink_ids(e)
    return ids


def _body(text: str) -> str:
    match = _FENCE.search(text)
    return (match.group(1) if match else text).strip()


def _confidence(raw: Any) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("entry"), str):
            continue
        level = str(item.get("confidence", "")).strip().lower()
        if level in LEVELS:
            out.setdefault(item["entry"].strip(), (level, str(item.get("reason", "")).strip()))
    return out


def parse_response(text: str) -> Parsed:
    try:
        doc = load_yaml(_body(text))
    except (YamlError, RecursionError) as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else "invalid YAML"
        return Parsed(None, error=f"the reply is not valid YAML: {first}")
    if not isinstance(doc, dict):
        return Parsed(None, error="the reply is not a YAML mapping with a `mapping` key")
    if isinstance(doc.get("mapping"), dict):
        return Parsed(doc["mapping"], _confidence(doc.get("confidence")))
    if "downlinks" in doc or "baseline_version" in doc:
        return Parsed(doc, _confidence(doc.get("confidence")))  # a bare spec, no confidences
    return Parsed(None, error="the reply has no `mapping` key")
