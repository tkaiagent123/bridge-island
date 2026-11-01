"""`bridge-island describe`: what an example looks like, as text, Markdown, Mermaid or JSON."""

from __future__ import annotations

import re
from types import ModuleType
from typing import Any

import yaml

from bridge_island.codegen import load_module
from bridge_island.examples import generated_path, spec_path


def _transform_label(t: Any) -> str:
    if t == "identity":
        return "identity"
    ((kind, arg),) = t.items()
    if kind == "unit":
        return f"unit {arg[0]}->{arg[1]}"
    if kind == "valid_range":
        return f"range {arg[0]}..{arg[1]}"
    if kind in ("bam_decode", "bam_encode"):
        return f"{kind.replace('_', ' ')} {arg}"
    if kind == "enum":
        return f"enum ({len(arg['map'])} values)"
    return str(kind).replace("_", " ")


def describe(world: ModuleType, version: int) -> dict[str, Any]:
    """The baseline interface, the subsystems, and every path between them, for one version."""
    dic = world.dictionary(version)
    spec = yaml.safe_load(spec_path(world, version).read_text(encoding="utf-8"))
    mapping = load_module(generated_path(world, version))
    identity = {(ref, f"{sub}.{native}"): ident for ref, sub, native, ident in mapping.DOWNLINKS}

    def rep(r: Any) -> str:
        return f"{r.encoding}, {r.units}, {r.frame}"

    paths = []
    for entry in spec.get("downlinks", []):
        removed = entry.get("status") == "removed"
        transforms = [_transform_label(t) for t in entry.get("transforms", [])]
        paths.append(
            {
                "from": entry["from"],
                "to": entry["to"],
                "kind": "removed"
                if removed
                else ("identity" if identity[(entry["from"], entry["to"])] else "converted"),
                "transforms": transforms,
                "reason": entry.get("reason", "") if removed else "",
            }
        )
    return {
        "example": world.NAME,
        "version": version,
        "baseline": {
            "published": [
                {
                    "message": m.name,
                    "rate_hz": m.rate_hz,
                    "elements": [
                        {"name": e.name, "representation": rep(e.representation)}
                        for e in m.elements
                    ],
                }
                for m in dic.interface.published
            ],
            "accepted": [m.name for m in dic.interface.accepted],
        },
        "subsystems": [
            {
                "name": name,
                "protocol": next((n.representation.protocol for n in needs), "n/a"),
                "inputs": [
                    {
                        "name": n.native_name,
                        "representation": rep(n.representation),
                        "max_rate_hz": n.rate_hz,
                        "latency_budget_ms": round(1000 * n.latency_budget_s, 3),
                    }
                    for n in needs
                ],
                "outputs": [o.message_name for o in dic.outputs.get(name, ())],
            }
            for name, needs in dic.needs.items()
        ],
        "paths": paths,
        "uplinks": [
            {
                "from": u["from"],
                "to": u["to"],
                "fields": [f"{f['from']}->{f['to']}" for f in u["fields"]],
            }
            for u in spec.get("uplinks", [])
        ],
        "counts": {
            "published_elements": sum(len(m.elements) for m in dic.interface.published),
            "subsystems": len(dic.needs),
            "paths": len(paths),
            "identity": sum(1 for p in paths if p["kind"] == "identity"),
            "converted": sum(1 for p in paths if p["kind"] == "converted"),
            "removed": sum(1 for p in paths if p["kind"] == "removed"),
        },
    }


def render_text(d: dict[str, Any]) -> str:
    c = d["counts"]
    lines = [
        f"{d['example']}: baseline interface v{d['version']} "
        f"(all content invented; numbers are synthetic, illustrative)",
        f"{c['published_elements']} published elements, {c['subsystems']} unique subsystems, "
        f"{c['paths']} paths: {c['identity']} identity, {c['converted']} converted, {c['removed']} removed",
        "",
        "Baseline publishes:",
    ]
    for m in d["baseline"]["published"]:
        lines.append(
            f"  {m['message']:<18}{m['rate_hz']:>6g} Hz  "
            + ", ".join(e["name"] for e in m["elements"])
        )
    lines.append(f"Baseline accepts:   {', '.join(d['baseline']['accepted'])}")
    lines += ["", "Unique subsystems:"]
    for s in d["subsystems"]:
        out = f"; sends {', '.join(s['outputs'])}" if s["outputs"] else ""
        lines.append(f"  {s['name']:<17}{s['protocol']:<18}{len(s['inputs'])} inputs{out}")
    lines += ["", "Paths (baseline element -> subsystem input):"]
    width = max(len(p["from"]) for p in d["paths"])
    for p in d["paths"]:
        how = {"identity": "identity", "removed": f"REMOVED: {p['reason']}"}.get(
            p["kind"], ", ".join(p["transforms"])
        )
        lines.append(f"  {p['from']:<{width}}  ->  {p['to']:<34}{how}")
    for u in d["uplinks"]:
        lines.append(f"  (uplink) {u['from']} -> {u['to']}: {', '.join(u['fields'])}")
    return "\n".join(lines)


def render_markdown(d: dict[str, Any]) -> str:
    lines = [
        f"# {d['example']}: baseline interface v{d['version']}",
        "",
        "*All content invented; numbers synthetic, illustrative.*",
        "",
        "| From | To | Path |",
        "| --- | --- | --- |",
    ]
    for p in d["paths"]:
        how = (
            "identity"
            if p["kind"] == "identity"
            else ("removed" if p["kind"] == "removed" else ", ".join(p["transforms"]))
        )
        lines.append(f"| `{p['from']}` | `{p['to']}` | {how} |")
    return "\n".join(lines)


def _id(prefix: str, name: str) -> str:
    return prefix + re.sub(r"\W", "_", name)


def render_mermaid(d: dict[str, Any]) -> str:
    lines = ["flowchart LR", '  subgraph baseline["Baseline Island"]']
    for m in d["baseline"]["published"]:
        lines.append(f'    {_id("B_", m["message"])}["{m["message"]}<br/>{m["rate_hz"]:g} Hz"]')
    lines += [
        "  end",
        '  subgraph bridge["Bridge"]',
        '    OIU["OIU (remote terminal)"] --> TM["Translator Module"] --> UID["UID"]',
        "  end",
        '  subgraph unique["Unique Island"]',
    ]
    for s in d["subsystems"]:
        lines.append(f'    {_id("U_", s["name"])}["{s["name"]}<br/>{s["protocol"]}"]')
    lines.append("  end")
    for m in d["baseline"]["published"]:
        lines.append(f"  {_id('B_', m['message'])} --> OIU")
    for s in d["subsystems"]:
        mine = [p for p in d["paths"] if p["to"].startswith(s["name"] + ".")]
        conv = sum(1 for p in mine if p["kind"] == "converted")
        gone = sum(1 for p in mine if p["kind"] == "removed")
        label = f"{len(mine)} inputs, {conv} converted" + (f", {gone} removed" if gone else "")
        lines.append(f'  UID -->|"{label}"| {_id("U_", s["name"])}')
    return "\n".join(lines)
