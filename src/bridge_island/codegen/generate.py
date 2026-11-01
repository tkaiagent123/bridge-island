"""Turn a YAML mapping spec into a generated Python module (G5).

The generated module holds straight-line conversion code and a few descriptive constants.
There are no run-time lookup tables for the mapping. The build fails loudly, with a
:class:`CodegenError`, if the spec is malformed or does not fit the baseline interface and the
unique systems' declared needs. Nothing else is raised for bad input.
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

from bridge_island.baseline.platform import BaselineInterface
from bridge_island.core.elements import Representation
from bridge_island.core.transforms import Chain, TransformError, ValidityCheck, chain, from_spec
from bridge_island.unique.subsystem import Need, Output
from bridge_island.yamlutil import YamlError, load_yaml


class CodegenError(Exception):
    """The spec is invalid or does not fit the interfaces it maps."""


@dataclass(frozen=True)
class Dictionary:
    """What the generator needs to know about both islands for one baseline version."""

    interface: BaselineInterface
    needs: Mapping[str, tuple[Need, ...]]
    outputs: Mapping[str, tuple[Output, ...]] = field(default_factory=dict)


@dataclass
class _Parts:
    """What the generator collects before it writes the module."""

    downlinks: list[tuple[str, str, str, bool]] = field(default_factory=list)
    unavailable: list[tuple[str, str, str, str]] = field(default_factory=list)
    uplinks: list[tuple[str, str, str]] = field(default_factory=list)
    body: dict[str, list[str]] = field(default_factory=dict)  # baseline message -> code lines
    up_code: list[str] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)


def spec_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# --- reading and checking the spec --------------------------------------------------------


def _read_spec(spec_path: Path) -> dict[str, Any]:
    try:
        text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CodegenError(
            f"cannot read {spec_path}: {getattr(exc, 'strerror', None) or exc}"
        ) from exc
    try:
        spec = load_yaml(text)
    except YamlError as exc:
        raise CodegenError(f"{spec_path.name}: {exc}") from exc
    if not isinstance(spec, dict) or "name" not in spec or "baseline_version" not in spec:
        raise CodegenError("spec needs 'name' and 'baseline_version'")
    return spec


def _entries(
    spec: Mapping[str, Any], section: str, required: tuple[str, ...]
) -> list[dict[str, Any]]:
    raw = spec.get(section)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CodegenError(f"'{section}' must be a list")
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in required):
            raise CodegenError(
                f"{section}[{i}] must be a mapping with text values for {list(required)}"
            )
    return raw


def _chain(entry: Mapping[str, Any], src: Representation, dst: Representation, where: str) -> Chain:
    steps = entry.get("transforms", [])
    if not isinstance(steps, list):
        raise CodegenError(f"{where}: 'transforms' must be a list")
    try:
        ch = chain(*(from_spec(t) for t in steps))
        out = ch.output(src)
    except TransformError as exc:
        raise CodegenError(f"{where}: {exc}") from exc
    translates = _translates(ch)
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


def _translates(ch: Chain) -> bool:
    """True if the chain changes the value. A range check does not: the value passes unchanged."""
    return any(not (s.is_identity or isinstance(s, ValidityCheck)) for s in ch.steps)


def _steps(ch: Chain, src_var: str, indent: str) -> tuple[list[str], str]:
    """One assignment per transform step. Returns the lines and the final variable name."""
    lines, var = [], src_var
    for i, step in enumerate(ch.steps, 1):
        new = f"v{i}"
        lines.append(f"{indent}{new} = {step.expr(var)}")
        var = new
    return lines, var


# --- downlinks and uplinks ----------------------------------------------------------------


def _downlink(entry: Mapping[str, Any], dic: Dictionary, parts: _Parts) -> None:
    ref, to = entry["from"], entry["to"]
    where = f"{ref} -> {to}"
    sub, _, native = to.partition(".")
    need = next((n for n in dic.needs.get(sub, ()) if n.native_name == native), None)
    if need is None:
        raise CodegenError(f"{where}: {to} is not a declared input of a unique system")
    if to in parts.covered:
        raise CodegenError(f"{where}: {to} is mapped twice")
    parts.covered.add(to)
    element = dic.interface.element(ref)
    status = entry.get("status")
    if status == "removed":
        if element is not None:
            raise CodegenError(
                f"{where}: marked removed but baseline v{dic.interface.version} still publishes it"
            )
        reason = str(entry.get("reason", "removed from baseline"))
        parts.unavailable.append((ref, sub, native, reason))
        return
    if status is not None:
        raise CodegenError(f"{where}: unknown status {status!r}")
    if element is None:
        raise CodegenError(
            f"{where}: {ref} is not published by baseline v{dic.interface.version}; "
            "delete the entry or mark it 'status: removed'"
        )
    ch = _chain(entry, element.representation, need.representation, where)
    identity = not _translates(ch)
    parts.downlinks.append((ref, sub, native, identity))
    message, _, fld = ref.partition(".")
    lines, var = _steps(ch, "v0", " " * 12)
    parts.body.setdefault(message, []).extend(
        [
            "        try:",
            f"            v0 = values[{fld!r}]",
            *lines,
            f"            out.append(({ref!r}, {sub!r}, {native!r}, {var}, {identity!r}))",
            "        except (TransformError, KeyError) as exc:",
            f"            errors.append(({ref!r}, str(exc)))",
        ]
    )


def _uplink(entry: Mapping[str, Any], dic: Dictionary, parts: _Parts) -> None:
    sub, _, umsg = entry["from"].partition(".")
    bmsg = entry["to"]
    where = f"{entry['from']} -> {bmsg}"
    out = next((o for o in dic.outputs.get(sub, ()) if o.message_name == umsg), None)
    accepted = dic.interface.accepted_message(bmsg)
    if out is None:
        raise CodegenError(f"{where}: {sub} does not declare output {umsg!r}")
    if accepted is None:
        raise CodegenError(f"{where}: baseline v{dic.interface.version} does not accept {bmsg!r}")
    fields = entry.get("fields")
    if not isinstance(fields, list) or not all(
        isinstance(f, dict) and isinstance(f.get("from"), str) and isinstance(f.get("to"), str)
        for f in fields
    ):
        raise CodegenError(
            f"{where}: 'fields' must be a list of mappings with text 'from' and 'to'"
        )
    if sorted(f["from"] for f in fields) != sorted(n for n, _ in out.fields):
        raise CodegenError(f"{where}: every field of {umsg} must be mapped exactly once")
    if sorted(f["to"] for f in fields) != sorted(e.name for e in accepted.elements):
        raise CodegenError(f"{where}: every field of {bmsg} must be filled exactly once")
    parts.uplinks.append((sub, umsg, bmsg))
    expected = "{" + ", ".join(repr(x) for x in sorted(f["from"] for f in fields)) + "}"
    parts.up_code += [
        f"    if sender == {sub!r} and message_name == {umsg!r}:",
        f"        if set(values) != {expected}:",
        f"            raise MalformedError({umsg + ': unexpected or missing fields'!r})",
        "        out: dict[str, Any] = {}",
    ]
    reps = dict(out.fields)
    for f in fields:
        target = accepted.element(f["to"])
        if target is None:  # unreachable: the sets were checked equal above
            raise CodegenError(f"{where}: {bmsg} has no field {f['to']!r}")
        ch = _chain(f, reps[f["from"]], target.representation, f"{where} [{f['from']}]")
        lines, var = _steps(ch, "v0", " " * 8)
        parts.up_code += [
            f"        check_type(values[{f['from']!r}], {reps[f['from']].encoding!r})",
            f"        v0 = values[{f['from']!r}]",
            *lines,
            f"        out[{f['to']!r}] = {var}",
        ]
    parts.up_code.append(f"        return {bmsg!r}, out")


# --- writing the module -------------------------------------------------------------------

_HEADER_IMPORTS = [
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
]


def _render(spec: Mapping[str, Any], spec_path: Path, version: int, parts: _Parts) -> str:
    sha = spec_sha256(spec_path)
    lines = [
        "# GENERATED FILE - DO NOT EDIT.",
        f"# Generated by bridge-island codegen from spec: {spec_path.name}",
        f"# Spec SHA-256: {sha}",
        *_HEADER_IMPORTS,
        f"SPEC_NAME = {spec['name']!r}",
        f"SPEC_FILE = {spec_path.name!r}",
        f"SPEC_SHA256 = {sha!r}",
        f"BASELINE_VERSION = {version!r}",
        "",
        "# (baseline element, subsystem, native name, identity)",
        "DOWNLINKS = (",
        *[f"    {d!r}," for d in parts.downlinks],
        ")",
        "",
        "# (baseline element, subsystem, native name, reason)",
        "UNAVAILABLE = (",
        *[f"    {u!r}," for u in parts.unavailable],
        ")",
        "",
        "# (subsystem, unique message, baseline message)",
        "UPLINKS = (",
        *[f"    {u!r}," for u in parts.uplinks],
        ")",
        "",
        "",
        "def translate(",
        "    message_name: str, values: Mapping[str, Any]",
        ") -> tuple[list[tuple[str, str, str, Any, bool]], list[tuple[str, str]]]:",
        "    out: list[tuple[str, str, str, Any, bool]] = []",
        "    errors: list[tuple[str, str]] = []",
    ]
    for i, (message, code) in enumerate(parts.body.items()):
        lines.append(f"    {'if' if i == 0 else 'elif'} message_name == {message!r}:")
        lines += code
    lines += [
        "    return out, errors",
        "",
        "",
        "def translate_up(",
        "    sender: str, message_name: str, values: Mapping[str, Any]",
        ") -> tuple[str, dict[str, Any]]:",
        *parts.up_code,
        '    raise UnmappedError(f"{sender}.{message_name} is not mapped")',
        "",
    ]
    return "\n".join(lines)


def generate(spec_path: Path, dic: Dictionary) -> str:
    """The text of the generated module for a spec, or a CodegenError saying what to fix."""
    spec = _read_spec(spec_path)
    if spec["baseline_version"] != dic.interface.version:
        raise CodegenError(
            f"spec is for baseline v{spec['baseline_version']}, "
            f"interface is v{dic.interface.version}"
        )
    parts = _Parts()
    for entry in _entries(spec, "downlinks", ("from", "to")):
        _downlink(entry, dic, parts)
    for sub, needs in dic.needs.items():
        for n in needs:
            if f"{sub}.{n.native_name}" not in parts.covered:
                raise CodegenError(
                    f"{sub}.{n.native_name} is a declared input but has no mapping entry"
                )
    for entry in _entries(spec, "uplinks", ("from", "to")):
        _uplink(entry, dic, parts)
    return _render(spec, spec_path, dic.interface.version, parts)


def generate_file(spec_path: Path, dic: Dictionary, out_path: Path | None = None) -> Path:
    out_path = out_path or spec_path.parent / "generated" / f"{spec_path.stem}.py"
    text = generate(spec_path, dic)  # generate first: a failed build must not leave an empty file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")
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
    spec = _read_spec(spec_path)
    world_file = spec_path.parent / str(spec.get("world", "world.py"))
    if not world_file.is_file():
        raise CodegenError(f"the spec's world file {world_file} does not exist")
    version = spec["baseline_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise CodegenError(f"baseline_version must be an integer, got {version!r}")
    dic = load_module(world_file).dictionary(version)
    if not isinstance(dic, Dictionary):
        raise CodegenError(f"{world_file.name}: dictionary({version}) did not return a Dictionary")
    return dic
