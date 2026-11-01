"""`bridge-island diff`: what a baseline version change touches, measured on the real files."""

from __future__ import annotations

import difflib
from types import ModuleType
from typing import Any

from bridge_island.analysis.change_impact import diff_interfaces, impact_bridge, impact_p2p
from bridge_island.analysis.topology import edges_from_mapping
from bridge_island.codegen import load_module
from bridge_island.examples import generated_path, spec_path


def _file_diff(a: str, b: str, name_a: str, name_b: str) -> dict[str, Any]:
    lines = list(
        difflib.unified_diff(a.splitlines(), b.splitlines(), name_a, name_b, lineterm="", n=0)
    )
    body = [ln for ln in lines if not ln.startswith(("---", "+++", "@@"))]
    # count substantive lines only: comments and blank lines are not behaviour
    real = [ln for ln in body if ln[1:].strip() and not ln[1:].lstrip().startswith("#")]
    return {
        "added": sum(1 for ln in real if ln.startswith("+")),
        "removed": sum(1 for ln in real if ln.startswith("-")),
        "added_including_comments": sum(1 for ln in body if ln.startswith("+")),
        "removed_including_comments": sum(1 for ln in body if ln.startswith("-")),
        "unified": "\n".join(lines),
    }


def diff_versions(world: ModuleType, old: int, new: int) -> dict[str, Any]:
    """Interface changes, the mapping-spec lines that change (measured), and the modelled impact."""
    changes = diff_interfaces(world.interface(old), world.interface(new), world.RENAMES)
    edges = edges_from_mapping(load_module(generated_path(world, old)))
    spec_a, spec_b = spec_path(world, old), spec_path(world, new)
    removed_subs = sorted(
        {s for ref, s in edges if any(c.kind == "remove" and c.ref == ref for c in changes)}
    )
    return {
        "example": world.NAME,
        "from_version": old,
        "to_version": new,
        "changes": [{"kind": c.kind, "ref": c.ref} for c in changes],
        "mapping_spec": {
            "file": spec_b.name,
            **_file_diff(
                spec_a.read_text(encoding="utf-8"),
                spec_b.read_text(encoding="utf-8"),
                spec_a.name,
                spec_b.name,
            ),
        },
        "unique_subsystems_that_must_handle_a_removal": removed_subs,
        "modelled_impact": {
            "p2p": vars(impact_p2p(changes, edges)),
            "bridge": vars(impact_bridge(changes, edges)),
        },
    }


def render_text(d: dict[str, Any], full: bool = False) -> str:
    spec = d["mapping_spec"]
    lines = [
        f"{d['example']}: baseline interface v{d['from_version']} -> v{d['to_version']} "
        f"(all content invented; numbers are synthetic, illustrative)",
        "",
        f"{len(d['changes'])} interface changes:",
    ]
    lines += [f"  {c['kind']:<9}{c['ref']}" for c in d["changes"]]
    lines += [
        "",
        f"On the Bridge, the change lands in one file, measured: {spec['file']} changes by "
        f"+{spec['added']} / -{spec['removed']} lines (comments and blank lines not counted). No unique-system file changes, except that "
        f"{', '.join(d['unique_subsystems_that_must_handle_a_removal']) or 'none'} must handle the removed element.",
    ]
    diff_lines = spec["unified"].splitlines()
    shown = diff_lines if full else diff_lines[:30]
    lines += ["", *shown]
    if len(shown) < len(diff_lines):
        lines.append(f"... {len(diff_lines) - len(shown)} more lines (use --full)")
    p, b = d["modelled_impact"]["p2p"], d["modelled_impact"]["bridge"]
    lines += [
        "",
        "Modelled impact (counting model, see docs/experiments.md):",
        f"{'':<38}{'P2P':>5}{'Bridge':>8}",
    ]
    for key, label in [
        ("subsystems_code_change", "unique subsystems needing code change"),
        ("modules_touched", "modules touched"),
        ("tests_failing", "tests failing before the fix"),
        ("components_reverified", "components needing re-verification"),
    ]:
        lines.append(f"  {label:<36}{p[key]:>5}{b[key]:>8}")
    return "\n".join(lines)
