"""Robustness variants of a task, so that a good score is not just recognising familiar names.

* ``plain``: the example as written.
* ``obfuscated``: every element, message and input name is replaced by a neutral token. Units,
  encodings, frames and ranges are kept. The same seeded renaming is applied to the interfaces, the
  systems' needs, the old mapping and the answer.
* ``perturbed``: seeded random change sets applied to the old interface (renames, unit and encoding
  changes, added, removed and re-rated elements). The answer is *derived* from the old mapping plus
  the change set, with the same inference the heuristic uses, and checked with the contract check.
  Because the derivation shares that inference, the heuristic is expected to do well on unit
  changes here.
"""

from __future__ import annotations

import random
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from bridge_island.aimap.heuristic import infer_steps
from bridge_island.aimap.score import contract_check
from bridge_island.aimap.task import Task
from bridge_island.baseline.platform import BaselineInterface
from bridge_island.core import units
from bridge_island.core.elements import DataElement, MessageSpec
from bridge_island.unique.subsystem import Output

VARIANTS = ("plain", "obfuscated", "perturbed")


# ---- obfuscated --------------------------------------------------------------------------------


def _tokens(names: list[str], prefix: str, rng: random.Random, width: int = 2) -> dict[str, str]:
    order = sorted(set(names))
    rng.shuffle(order)
    return {name: f"{prefix}{i:0{width}d}" for i, name in enumerate(order, 1)}


def obfuscate(task: Task, seed: int) -> Task:
    """The same task with neutral names. Needs no change to scoring: it is just another task."""
    rng = random.Random(f"obfuscate/{task.example}/{seed}")
    both = (task.v1, task.v2)
    pub = _tokens([m.name for i in both for m in i.published], "M", rng)
    acc = _tokens([m.name for i in both for m in i.accepted], "A", rng)
    fields: dict[str, dict[str, str]] = {}
    for i in both:
        for m in (*i.published, *i.accepted):
            fields.setdefault(m.name, {})
    for name in fields:
        names = [
            e.name
            for i in both
            for m in (*i.published, *i.accepted)
            if m.name == name
            for e in m.elements
        ]
        fields[name] = _tokens(names, "F", rng, 1)
    inputs = _tokens([n.native_name for ns in task.needs.values() for n in ns], "N", rng, 3)
    outs = _tokens([o.message_name for os in task.outputs.values() for o in os], "O", rng)
    out_fields: dict[str, dict[str, str]] = {
        o.message_name: _tokens([n for n, _ in o.fields], "P", rng, 1)
        for os in task.outputs.values()
        for o in os
    }

    def message(m: MessageSpec, names: Mapping[str, str]) -> MessageSpec:
        new = names[m.name]
        elements = tuple(replace(e, name=fields[m.name][e.name]) for e in m.elements)
        return replace(m, name=new, elements=elements)

    def interface(i: BaselineInterface) -> BaselineInterface:
        return replace(
            i,
            published=tuple(message(m, pub) for m in i.published),
            accepted=tuple(message(m, acc) for m in i.accepted),
        )

    def ref(r: str) -> str:
        msg, _, el = r.partition(".")
        if msg in pub and el in fields.get(msg, {}):
            return f"{pub[msg]}.{fields[msg][el]}"
        return r  # a ref to something that was never published, such as a removed element

    def need_ref(r: str) -> str:
        sub, _, native = r.partition(".")
        return f"{sub}.{inputs.get(native, native)}"

    def down(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {**entry, "from": ref(str(entry["from"])), "to": need_ref(str(entry["to"]))}

    def up(entry: Mapping[str, Any]) -> dict[str, Any]:
        sub, _, omsg = str(entry["from"]).partition(".")
        bmsg = str(entry["to"])
        ofields, bfields = out_fields.get(omsg, {}), fields.get(bmsg, {})
        return {
            **entry,
            "from": f"{sub}.{outs.get(omsg, omsg)}",
            "to": acc.get(bmsg, bmsg),
            "fields": [
                {
                    **f,
                    "from": ofields.get(f["from"], f["from"]),
                    "to": bfields.get(f["to"], f["to"]),
                }
                for f in entry.get("fields") or []
            ],
        }

    def spec(s: Mapping[str, Any], name: str) -> dict[str, Any]:
        out = {k: v for k, v in s.items() if k != "world"}
        out["name"] = name
        out["downlinks"] = [down(e) for e in s.get("downlinks") or []]
        out["uplinks"] = [up(e) for e in s.get("uplinks") or []]
        return out

    needs = {
        sub: tuple(replace(n, native_name=inputs[n.native_name]) for n in ns)
        for sub, ns in task.needs.items()
    }
    outputs = {
        sub: tuple(
            Output(
                outs[o.message_name],
                tuple((out_fields[o.message_name][n], r) for n, r in o.fields),
            )
            for o in os
        )
        for sub, os in task.outputs.items()
    }
    return replace(
        task,
        variant="obfuscated",
        v1=interface(task.v1),
        v2=interface(task.v2),
        needs=needs,
        outputs=outputs,
        v1_spec=spec(task.v1_spec, "obfuscated_v1"),
        truth=spec(task.truth, "obfuscated_v2"),
        notes=tuple((ref(r), text) for r, text in task.notes),
        world=None,
    )


# ---- perturbed ---------------------------------------------------------------------------------

KINDS = ("rename", "unit", "encoding", "rate", "add", "remove")


def _alternative_units(unit: str) -> list[str]:
    table = units._UNITS
    if unit not in table:
        return []
    return sorted(u for u, (dim, _, _) in table.items() if dim == table[unit][0] and u != unit)


def _new_name(name: str, taken: set[str]) -> str:
    tokens = name.split("_")
    candidate = (
        tokens[0] + tokens[1] + "".join("_" + t for t in tokens[2:])
        if len(tokens) > 1
        else f"{name}_ext"
    )
    while candidate in taken:
        candidate += "_x"
    return candidate


def _once(base: Task, rng: random.Random, tag: str) -> Task | None:
    """One random change set applied to the old interface; None if the derived answer is invalid."""
    used = {str(e["from"]) for e in base.v1_spec.get("downlinks") or []}
    guarded = {  # sources of entries with a range check or an enumeration: leave their form alone
        str(e["from"])
        for e in base.v1_spec.get("downlinks") or []
        if any(
            isinstance(t, dict) and ({"valid_range", "enum"} & set(t))
            for t in e.get("transforms") or []
        )
    }
    elements = {m.name: list(m.elements) for m in base.v1.published}

    def origin(msg: str, name: str) -> str:
        """The element's ref in the old interface, however often it has been renamed since."""
        return next((k for k, v in renames.items() if v == f"{msg}.{name}"), f"{msg}.{name}")

    rates = {m.name: m.rate_hz for m in base.v1.published}
    renames: dict[str, str] = {}
    removed: set[str] = set()
    for _ in range(rng.choice((1, 2, 3))):
        kind = rng.choice(KINDS)
        msg = rng.choice(sorted(elements))
        els = elements[msg]
        pool = [e for e in els if f"{msg}.{e.name}" not in removed]
        if kind == "rate":
            rates[msg] = rates[msg] * 2
        elif kind == "add":
            taken = {e.name for e in els}
            name = _new_name("added", taken)
            els.append(
                DataElement(name, replace(els[0].representation, units="1", frame="f"), None)
            )
        elif kind == "remove" and len(pool) > 1:
            gone = rng.choice([e for e in pool if f"{msg}.{e.name}" in used] or pool)
            removed.add(origin(msg, gone.name))
            els.remove(gone)
        elif kind == "rename" and pool:
            e = rng.choice(pool)
            original = origin(msg, e.name)
            new = _new_name(e.name, {x.name for x in els})
            els[els.index(e)] = replace(e, name=new)
            renames[original] = f"{msg}.{new}"
        elif kind == "unit" and pool:
            e = rng.choice(pool)
            rep = e.representation
            source = origin(msg, e.name)
            options = _alternative_units(rep.units)
            if rep.encoding == "float64" and options and source not in guarded:
                new_unit = rng.choice(options)
                scale, offset = units.coefficients(rep.units, new_unit)
                rng_ = e.valid_range
                new_range = (rng_[0] * scale + offset, rng_[1] * scale + offset) if rng_ else None
                els[els.index(e)] = replace(
                    e, representation=replace(rep, units=new_unit), valid_range=new_range
                )
        elif kind == "encoding" and pool:
            e = rng.choice(pool)
            rep = e.representation
            source = origin(msg, e.name)
            if source not in guarded and rep.encoding == "float64" and rep.units == "deg":
                els[els.index(e)] = replace(
                    e, representation=replace(rep, encoding="bam16"), valid_range=None
                )
            elif source not in guarded and rep.encoding == "bam16":
                els[els.index(e)] = replace(
                    e, representation=replace(rep, encoding="float64"), valid_range=(0, 360)
                )
    v2 = BaselineInterface(
        2,
        tuple(
            MessageSpec(m.name, tuple(elements[m.name]), rates[m.name], m.subaddress)
            for m in base.v1.published
        ),
        base.v1.accepted,
    )
    downlinks: list[dict[str, Any]] = []
    for old in base.v1_spec.get("downlinks") or []:
        ref = str(old["from"])
        if ref in removed:
            downlinks.append(
                {
                    **{k: old[k] for k in ("from", "to")},
                    "status": "removed",
                    "reason": "element removed from baseline interface v2",
                }
            )
            continue
        new_ref = renames.get(ref, ref)
        element, before = v2.element(new_ref), base.v1.element(ref)
        sub, _, native = str(old["to"]).partition(".")
        need = next(n for n in base.needs[sub] if n.native_name == native)
        if element is None or before is None:
            return None
        same_form = before.representation.same_value_form(element.representation)
        steps = infer_steps(
            element.representation, need.representation, old.get("transforms") or [], same_form
        )
        downlinks.append(
            {"from": new_ref, "to": old["to"], **({"transforms": steps} if steps else {})}
        )
    truth = {
        "name": f"perturbed_{tag}_v2",
        "baseline_version": 2,
        "downlinks": downlinks,
        "uplinks": base.v1_spec.get("uplinks") or [],
    }
    task = replace(base, variant="perturbed", case=tag, v2=v2, truth=truth, notes=(), world=None)
    with tempfile.TemporaryDirectory(prefix="aimap_") as tmp:
        error, path = contract_check(task, truth, Path(tmp))
    return task if path is not None and not error else None


def perturbed_tasks(base: Task, seed: int, n: int) -> list[Task]:
    """``n`` perturbed cases of ``base`` (a plain task); the same arguments give the same cases."""
    cases: list[Task] = []
    for i in range(1, n + 1):
        tag = f"p{i:02d}"
        for attempt in range(200):
            rng = random.Random(f"perturb/{base.example}/{seed}/{i}/{attempt}")
            task = _once(base, rng, tag)
            if task is not None:
                cases.append(task)
                break
        else:
            raise RuntimeError(f"could not derive a valid perturbed case {tag}")
    return cases


def make_tasks(base: Task, variant: str, seed: int, perturbed_n: int) -> list[Task]:
    if variant == "plain":
        return [base]
    if variant == "obfuscated":
        return [obfuscate(base, seed)]
    if variant == "perturbed":
        return perturbed_tasks(base, seed, perturbed_n)
    raise KeyError(variant)
