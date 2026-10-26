"""Turn a YAML mapping spec into a generated Python module (G5).

The generated module holds straight-line conversion code and a few descriptive constants.
There are no run-time lookup tables for the mapping. The build fails loudly if the spec does
not fit the baseline interface and the unique systems' declared needs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from bridge_island.baseline.platform import BaselineInterface
from bridge_island.core.elements import Representation
from bridge_island.core.transforms import Chain, TransformError, ValidityCheck, chain, from_spec
from bridge_island.unique.subsystem import Need, Output


class CodegenError(Exception):
    """The spec is invalid or does not fit the interfaces it maps."""


@dataclass(frozen=True)
class Dictionary:
    """What the generator needs to know about both islands for one baseline version."""

    interface: BaselineInterface
    needs: Mapping[str, tuple[Need, ...]]
    outputs: Mapping[str, tuple[Output, ...]] = field(default_factory=dict)


def spec_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _chain(entry: Mapping[str, Any], src: Representation, dst: Representation, where: str) -> Chain:
    try:
        ch = chain(*(from_spec(t) for t in entry.get("transforms", [])))
        out = ch.output(src)
    except TransformError as exc:
        raise CodegenError(f"{where}: {exc}") from exc
    translates = any(not (s.is_identity or isinstance(s, ValidityCheck)) for s in ch.steps)
    if src.same_value_form(dst) and translates:
        raise CodegenError(
            f"{where}: representations already match; translate only where needed (G2)"
        )
    if not out.same_value_form(dst):
        raise CodegenError(
            f"{where}: transforms give ({out.encoding}, {out.units}, {out.frame}) "
            f"but the target needs ({dst.encoding}, {dst.units}, {dst.frame})"
        )
    return ch


def _steps(ch: Chain, src_var: str, indent: str) -> tuple[list[str], str]:
    """One assignment per transform step. Returns the lines and the final variable name."""
    lines, var = [], src_var
    for i, step in enumerate(ch.steps, 1):
        new = f"v{i}"
        lines.append(f"{indent}{new} = {step.expr(var)}")
        var = new
    return lines, var


def generate(spec_path: Path, dic: Dictionary) -> str:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or "name" not in spec or "baseline_version" not in spec:
        raise CodegenError("spec needs 'name' and 'baseline_version'")
    if spec["baseline_version"] != dic.interface.version:
        raise CodegenError(
            f"spec is for baseline v{spec['baseline_version']}, "
            f"interface is v{dic.interface.version}"
        )

    downlinks: list[tuple[str, str, str, bool]] = []
    unavailable: list[tuple[str, str, str, str]] = []
    body: dict[str, list[str]] = {}  # baseline message -> code lines
    covered: set[str] = set()

    for entry in spec.get("downlinks", []):
        ref, to = str(entry["from"]), str(entry["to"])
        where = f"{ref} -> {to}"
        sub, _, native = to.partition(".")
        need = next((n for n in dic.needs.get(sub, ()) if n.native_name == native), None)
        if need is None:
            raise CodegenError(f"{where}: {to} is not a declared input of a unique system")
        if to in covered:
            raise CodegenError(f"{where}: {to} is mapped twice")
        covered.add(to)
        element = dic.interface.element(ref)
        status = entry.get("status")
        if status == "removed":
            if element is not None:
                raise CodegenError(
                    f"{where}: marked removed but baseline v{dic.interface.version} "
                    "still publishes it"
                )
            unavailable.append(
                (ref, sub, native, str(entry.get("reason", "removed from baseline")))
            )
            continue
        if status is not None:
            raise CodegenError(f"{where}: unknown status {status!r}")
        if element is None:
            raise CodegenError(
                f"{where}: {ref} is not published by baseline v{dic.interface.version}; "
                "delete the entry or mark it 'status: removed'"
            )
        ch = _chain(entry, element.representation, need.representation, where)
        identity = not any(not (s.is_identity or isinstance(s, ValidityCheck)) for s in ch.steps)
        downlinks.append((ref, sub, native, identity))
        message, _, fld = ref.partition(".")
        lines, var = _steps(ch, "v0", " " * 12)
        body.setdefault(message, []).extend(
            [
                "        try:",
                f"            v0 = values[{fld!r}]",
                *lines,
                f"            out.append(({ref!r}, {sub!r}, {native!r}, {var}, {identity!r}))",
                "        except (TransformError, KeyError) as exc:",
                f"            errors.append(({ref!r}, str(exc)))",
            ]
        )

    for sub, needs in dic.needs.items():
        for n in needs:
            if f"{sub}.{n.native_name}" not in covered:
                raise CodegenError(
                    f"{sub}.{n.native_name} is a declared input but has no mapping entry"
                )

    uplinks: list[tuple[str, str, str]] = []
    up_code: list[str] = []
    for entry in spec.get("uplinks", []):
        sub, _, umsg = str(entry["from"]).partition(".")
        bmsg = str(entry["to"])
        where = f"{entry['from']} -> {bmsg}"
        out = next((o for o in dic.outputs.get(sub, ()) if o.message_name == umsg), None)
        accepted = dic.interface.accepted_message(bmsg)
        if out is None:
            raise CodegenError(f"{where}: {sub} does not declare output {umsg!r}")
        if accepted is None:
            raise CodegenError(
                f"{where}: baseline v{dic.interface.version} does not accept {bmsg!r}"
            )
        fields = entry["fields"]
        if {f["from"] for f in fields} != {n for n, _ in out.fields}:
            raise CodegenError(f"{where}: every field of {umsg} must be mapped exactly once")
        if {f["to"] for f in fields} != {e.name for e in accepted.elements}:
            raise CodegenError(f"{where}: every field of {bmsg} must be filled exactly once")
        uplinks.append((sub, umsg, bmsg))
        expected = "{" + ", ".join(repr(x) for x in sorted(f["from"] for f in fields)) + "}"
        up_code += [
            f"    if sender == {sub!r} and message_name == {umsg!r}:",
            f"        if set(values) != {expected}:",
            f'            raise MalformedError("{umsg}: unexpected or missing fields")',
            "        out: dict[str, Any] = {}",
        ]
        reps = dict(out.fields)
        for f in fields:
            target = accepted.element(f["to"])
            assert target is not None
            ch = _chain(f, reps[f["from"]], target.representation, f"{where} [{f['from']}]")
            lines, var = _steps(ch, "v0", " " * 8)
            up_code += [
                f"        check_type(values[{f['from']!r}], {reps[f['from']].encoding!r})",
                f"        v0 = values[{f['from']!r}]",
                *lines,
                f"        out[{f['to']!r}] = {var}",
            ]
        up_code.append(f"        return {bmsg!r}, out")

    sha = spec_sha256(spec_path)
    lines = [
        "# GENERATED FILE - DO NOT EDIT.",
        f"# Generated by bridge-island codegen from spec: {spec_path.name}",
        f"# Spec SHA-256: {sha}",
        "from __future__ import annotations",
        "",
        "from collections.abc import Mapping",
        "from typing import Any",
        "",
        "from bridge_island.core.transforms import (",
        "    MalformedError,",
        "    TransformError,",
        "    UnmappedError,",
        "    check_range,",
        "    check_type,",
        "    enum_fail,",
        ")",
        "",
        f"SPEC_NAME = {spec['name']!r}",
        f"SPEC_FILE = {spec_path.name!r}",
        f"SPEC_SHA256 = {sha!r}",
        f"BASELINE_VERSION = {dic.interface.version!r}",
        "",
        "# (baseline element, subsystem, native name, identity)",
        "DOWNLINKS = (",
        *[f"    {d!r}," for d in downlinks],
        ")",
        "",
        "# (baseline element, subsystem, native name, reason)",
        "UNAVAILABLE = (",
        *[f"    {u!r}," for u in unavailable],
        ")",
        "",
        "# (subsystem, unique message, baseline message)",
        "UPLINKS = (",
        *[f"    {u!r}," for u in uplinks],
        ")",
        "",
        "",
        "def translate(",
        "    message_name: str, values: Mapping[str, Any]",
        ") -> tuple[list[tuple[str, str, str, Any, bool]], list[tuple[str, str]]]:",
        "    out: list[tuple[str, str, str, Any, bool]] = []",
        "    errors: list[tuple[str, str]] = []",
    ]
    for i, (message, code) in enumerate(body.items()):
        lines.append(f"    {'if' if i == 0 else 'elif'} message_name == {message!r}:")
        lines += code
    lines += [
        "    return out, errors",
        "",
        "",
        "def translate_up(",
        "    sender: str, message_name: str, values: Mapping[str, Any]",
        ") -> tuple[str, dict[str, Any]]:",
        *up_code,
        '    raise UnmappedError(f"{sender}.{message_name} is not mapped")',
        "",
    ]
    return "\n".join(lines)


def generate_file(spec_path: Path, dic: Dictionary, out_path: Path | None = None) -> Path:
    out_path = out_path or spec_path.parent / "generated" / f"{spec_path.stem}.py"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(generate(spec_path, dic), encoding="utf-8", newline="\n")
    return out_path


def load_module(path: Path) -> ModuleType:
    """Import a Python file by path (generated mappings and example worlds)."""
    name = f"_bridge_island_{path.parent.name}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CodegenError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def dictionary_for_spec(spec_path: Path) -> Dictionary:
    """Load the dictionary named by the spec's ``world`` file (default ``world.py``)."""
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    world = load_module(spec_path.parent / spec.get("world", "world.py"))
    dic = world.dictionary(spec["baseline_version"])
    assert isinstance(dic, Dictionary)
    return dic
