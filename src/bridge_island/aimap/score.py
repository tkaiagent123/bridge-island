"""Score a proposed mapping against the hand-written one, entry by entry, by *behaviour*.

Two entries agree when they read the same source element and give the same output (or both refuse
the value) for a set of test values, so a different spelling of the same conversion is correct and a
conversion that merely looks right but differs somewhere is not. A system run (plain variant only)
is a second, independent check on the whole proposal.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge_island.aimap.parse import downlink_id, uplink_ids
from bridge_island.aimap.task import Task, spec_text
from bridge_island.codegen import CodegenError, generate
from bridge_island.core.elements import Representation
from bridge_island.core.transforms import Chain, EnumMapping, chain, from_spec

CORRECT = "correct"
REJECTED = "rejected_by_contract"
UNPARSEABLE = "unparseable"
WRONG_FLAGGED = "wrong_flagged"
WRONG_UNFLAGGED = "wrong_unflagged"
MISSING = "missing"
EXTRA = "extra"
REMOVED_HANDLED = "removed_handled"
REMOVED_GUESSED = "removed_guessed"
CLASSES = (
    CORRECT,
    REJECTED,
    UNPARSEABLE,
    WRONG_FLAGGED,
    WRONG_UNFLAGGED,
    MISSING,
    EXTRA,
    REMOVED_HANDLED,
    REMOVED_GUESSED,
)
FLAGGED = ("low", "medium")  # a reviewer who reads only these never sees a "high" or unrated entry
NO_CONFIDENCE = "missing"  # the model gave none; counted as not flagged


@dataclass(frozen=True)
class EntryResult:
    entry: str  # the hand-written entry's identity, or the proposal's for an extra entry
    cls: str
    confidence: str  # high | medium | low | missing, or "" when there is no proposed entry
    reason: str = ""


# ---- behaviour of one chain of transforms ------------------------------------------------------


def _chain_of(entry: Mapping[str, Any]) -> Chain:
    return chain(*(from_spec(t) for t in entry.get("transforms") or []))


def _test_values(
    rep: Representation, valid: tuple[float, float] | None, chains: list[Chain]
) -> list[Any]:
    """Values to push through both chains: in range, at the edges and just outside."""
    enc = rep.encoding
    values: list[Any] = []
    if enc == "float64":
        lo, hi = valid if valid else (-1000.0, 1000.0)
        span = hi - lo
        values += [lo + span * k / 8 for k in range(9)]
        values += [lo - 0.1 * span, hi + 0.1 * span, 0.0, lo + 0.4137 * span]
    elif enc.startswith("bam") and enc[3:].isdigit():
        top = 1 << int(enc[3:])
        values += [0, 1, top // 4, top // 2, top - 1]
    elif enc.startswith(("uint", "int")):
        values += [0, 1, 2, 3, 4, 5, 255]
    else:
        values += [0, 1]
    enums = [st for ch in chains for st in ch.steps if isinstance(st, EnumMapping)]
    for step in enums:  # the codes the chains mention, and one they do not
        values += [k for k, _ in step.pairs]
    return [*values, "NOT_A_MEMBER"] if enums or enc == "enum" else values


def _outcome(ch: Chain, value: Any) -> tuple[bool, Any]:
    try:
        return True, ch.apply(value)
    except Exception:  # a transform that refuses a value is part of its behaviour
        return False, None


def _same(a: tuple[bool, Any], b: tuple[bool, Any]) -> bool:
    if a[0] != b[0]:
        return False
    if not a[0]:
        return True
    x, y = a[1], b[1]
    if isinstance(x, float) or isinstance(y, float):
        try:
            return math.isclose(float(x), float(y), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return bool(x == y)


def same_behaviour(
    rep: Representation,
    valid: tuple[float, float] | None,
    a: Mapping[str, Any],
    b: Mapping[str, Any],
) -> bool:
    ca, cb = _chain_of(a), _chain_of(b)
    return all(_same(_outcome(ca, v), _outcome(cb, v)) for v in _test_values(rep, valid, [ca, cb]))


# ---- classifying every hand-written entry ------------------------------------------------------


def _level(conf: Mapping[str, tuple[str, str]], entry_id: str) -> tuple[str, str]:
    level, why = conf.get(entry_id, (NO_CONFIDENCE, ""))
    return level, why


def _verdict(same: bool, level: str) -> str:
    if same:
        return CORRECT
    return WRONG_FLAGGED if level in FLAGGED else WRONG_UNFLAGGED


def fail_all(task: Task, cls: str) -> list[EntryResult]:
    """A proposal that was not parsed or not accepted: no entry of it can be used."""
    out = [EntryResult(str(e["to"]), cls, "") for e in task.truth.get("downlinks") or []]
    for up in task.truth.get("uplinks") or []:
        out += [EntryResult(f"{up['from']}:{f['from']}", cls, "") for f in up.get("fields") or []]
    return out


def score_accepted(
    task: Task, spec: Mapping[str, Any], conf: Mapping[str, tuple[str, str]]
) -> list[EntryResult]:
    """Entry classes for a proposal that passed the contract check."""
    results: list[EntryResult] = []
    proposed = {str(e["to"]): e for e in spec.get("downlinks") or [] if isinstance(e, dict)}
    truth_targets = {str(e["to"]) for e in task.truth.get("downlinks") or []}
    for t in task.truth.get("downlinks") or []:
        to = str(t["to"])
        p = proposed.get(to)
        if p is None:
            results.append(EntryResult(to, MISSING, ""))
            continue
        level, why = _level(conf, downlink_id(p))
        if t.get("status") == "removed":
            cls = REMOVED_HANDLED if p.get("status") == "removed" else REMOVED_GUESSED
        elif p.get("status") == "removed" or p.get("from") != t["from"]:
            cls = _verdict(False, level)
        else:
            element = task.v2.element(str(t["from"]))
            if element is None:  # unreachable: the hand-written mapping fits its own interface
                raise ValueError(f"{t['from']} is not in the new interface")
            cls = _verdict(same_behaviour(element.representation, element.valid_range, t, p), level)
        results.append(EntryResult(to, cls, level, why))
    for to, p in proposed.items():
        if to not in truth_targets:
            level, why = _level(conf, downlink_id(p))
            results.append(EntryResult(to, EXTRA, level, why))
    results += _score_uplinks(task, spec, conf)
    return results


def _score_uplinks(
    task: Task, spec: Mapping[str, Any], conf: Mapping[str, tuple[str, str]]
) -> list[EntryResult]:
    results: list[EntryResult] = []
    proposed = {str(u["from"]): u for u in spec.get("uplinks") or [] if isinstance(u, dict)}
    for tu in task.truth.get("uplinks") or []:
        sub, _, message = str(tu["from"]).partition(".")
        output = next((o for o in task.outputs.get(sub, ()) if o.message_name == message), None)
        reps = dict(output.fields) if output else {}
        pu = proposed.get(str(tu["from"]))
        pfields = {str(f["from"]): f for f in (pu or {}).get("fields") or [] if isinstance(f, dict)}
        for tf in tu.get("fields") or []:
            ident = f"{tu['from']}:{tf['from']}"
            pf = pfields.get(str(tf["from"]))
            if pu is None or pf is None:
                results.append(EntryResult(ident, MISSING, ""))
                continue
            level, why = _level(
                conf, uplink_ids({"from": pu["from"], "to": pu["to"], "fields": [pf]})[0]
            )
            same = (
                pu.get("to") == tu["to"]
                and pf.get("to") == tf["to"]
                and same_behaviour(reps[str(tf["from"])], None, tf, pf)
            )
            results.append(EntryResult(ident, _verdict(same, level), level, why))
    return results


# ---- the contract check, and the system-level cross-check ----------------------------------


def contract_check(task: Task, spec: Mapping[str, Any], workdir: Path) -> tuple[str, Path | None]:
    """Run the existing generator on a proposal. ``("", path)`` when it is accepted."""
    path = workdir / "proposal.yaml"
    try:
        path.write_text(spec_text(spec), encoding="utf-8", newline="\n")
        text = generate(path, task.dictionary)
    except CodegenError as exc:
        return str(exc), None
    except Exception as exc:  # a hostile proposal must not stop the experiment
        return f"unexpected {type(exc).__name__} in the contract check: {exc}", None
    out = workdir / "proposal_mapping.py"
    out.write_text(text, encoding="utf-8", newline="\n")
    return "", out


def _received(system: Any) -> dict[str, list[tuple[float, Any, bool]]]:
    seen: dict[str, list[tuple[float, Any, bool]]] = {}
    for name, sub in sorted(system.subsystems.items()):
        for r in sub.received:
            seen.setdefault(f"{name}.{r.native_name}", []).append(
                (round(r.datum.source_time, 6), r.datum.value, r.datum.estimated)
            )
        for native in sub.unavailable:
            seen.setdefault(f"{name}.{native}", []).append((-1.0, "unavailable", False))
    return seen


def system_differences(
    task: Task, mapping_py: Path, seed: int = 1, seconds: float = 4.0
) -> list[str]:
    """Inputs whose delivered data differ from the hand-written mapping's, after a short run."""
    if task.world is None:
        return []
    world = task.world
    reference = world.build(2, seed)
    reference.run(seconds)
    proposal = world.build(2, seed, mapping_file=mapping_py)
    proposal.run(seconds)
    want, got = _received(reference), _received(proposal)
    differing = []
    for key in sorted(set(want) | set(got)):
        a, b = want.get(key, []), got.get(key, [])
        if len(a) != len(b) or any(
            x[0] != y[0] or x[2] != y[2] or not _same((True, x[1]), (True, y[1]))
            for x, y in zip(a, b, strict=True)
        ):
            differing.append(key)
    return differing
