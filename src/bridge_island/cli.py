"""Command-line entry point (`bridge-island`).

Exit codes: 0 success; 1 a check failed (a guideline rule, or the code generator rejected a
spec); 2 invalid input or usage (a bad scenario file, an unknown option). Machine-readable output
(`--json`) goes to stdout; messages and errors go to stderr. Colour is off when output is not a
terminal, when NO_COLOR is set, or with --no-color.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from bridge_island import __version__
from bridge_island.examples import EXAMPLES, examples_dir, load_world
from bridge_island.scenario import ScenarioError
from bridge_island.term import color_enabled, paint

EXAMPLE_TEXT = """\
examples:
  bridge-island list                                 what is available
  bridge-island demo aviation                        run the aviation example, compare with P2P
  bridge-island describe aviation --version 2        every path from baseline to subsystems
  bridge-island diff aviation                        what the v1 -> v2 change touches
  bridge-island run examples/scenarios/stall_and_resume.yaml
  bridge-island report aviation --out report.html    one offline HTML file
  bridge-island check-rules --example medical        the G1-G6 checks
  bridge-island experiment change-impact --seed 1    reproducible experiments (CSV, plots)
  bridge-island codegen --spec examples/aviation_invented/mapping_v2.yaml

All content is invented and all numbers are synthetic, illustrative.
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bridge-island",
        description="Reference implementation of the bridge/island integration architecture.",
        epilog=EXAMPLE_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--no-color", action="store_true", help="never use colour (also: NO_COLOR=1)")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_, description=help_)

    sub.add_parser("list", help="list the examples and scenarios").add_argument(
        "--json", action="store_true", help="machine-readable output"
    )

    demo = add("demo", "run an invented example on both baseline versions and compare with P2P")
    demo.add_argument("example", choices=EXAMPLES)
    demo.add_argument("--seed", type=int, default=1)
    demo.add_argument(
        "--rate-policy",
        choices=["cap", "extrapolate"],
        default="cap",
        help="when a subsystem can use a higher rate than the baseline sends (default: cap)",
    )
    demo.add_argument("--json", action="store_true", help="machine-readable output")

    desc = add("describe", "show the baseline interface, the subsystems and every mapped path")
    desc.add_argument("example", choices=EXAMPLES)
    desc.add_argument("--version", dest="iface_version", type=int, help="baseline version")
    desc.add_argument("--format", choices=["text", "markdown", "mermaid", "json"], default="text")

    diff = add("diff", "what a baseline version change touches, measured on the real files")
    diff.add_argument("example", choices=EXAMPLES)
    diff.add_argument("--from", dest="old", type=int, help="older baseline version")
    diff.add_argument("--to", dest="new", type=int, help="newer baseline version")
    diff.add_argument("--format", choices=["text", "json"], default="text")
    diff.add_argument("--full", action="store_true", help="show the whole mapping diff")

    run = add("run", "run a scenario file (one reproducible run described in YAML)")
    run.add_argument("scenario", type=Path)
    run.add_argument("--json", action="store_true", help="machine-readable output")
    run.add_argument("--trace", type=Path, help="write every delivered datum to this CSV file")
    run.add_argument(
        "--out", type=Path, help="write summary.json, manifest.json and trace.csv here"
    )

    rep = add("report", "write one self-contained, offline HTML report for an example")
    rep.add_argument("example", choices=EXAMPLES)
    rep.add_argument("--out", type=Path, default=Path("report.html"))
    rep.add_argument("--seed", type=int, default=1)
    rep.add_argument("--duration", type=float, default=6.0, help="simulated seconds (default 6)")
    rep.add_argument("--rate-policy", choices=["cap", "extrapolate"], default="cap")
    rep.add_argument("--stall", metavar="AT,DURATION", help="stall the Bridge, e.g. 2.2,1.5")

    ui = add("ui", "open the app in your browser (forms and results; no command line needed)")
    ui.add_argument("--port", type=int, default=8765, help="first port to try (default 8765)")
    ui.add_argument("--no-browser", action="store_true", help="do not open the browser")

    gen = add("codegen", "compile a YAML mapping spec to a Python module (G5)")
    gen.add_argument("--spec", type=Path, required=True)
    gen.add_argument("--out", type=Path, help="default: <spec dir>/generated/<spec name>.py")

    exp = add("experiment", "run a reproducible experiment, writing CSV (and plots)")
    exp.add_argument("name", choices=["topology", "change-impact", "timing", "containment"])
    exp.add_argument("--seed", type=int, default=1)
    exp.add_argument("--out", type=Path, default=Path("outputs"))
    exp.add_argument("--example", choices=EXAMPLES, default="aviation")
    exp.add_argument("--trials", type=int, default=1000, help="change-impact Monte Carlo trials")

    rules = add("check-rules", "run the G1-G6 rule checks on an example")
    rules.add_argument("--example", choices=EXAMPLES, default="aviation")
    rules.add_argument("--version", dest="iface_version", type=int, help="baseline version")
    rules.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def _json(obj: Any) -> None:
    print(json.dumps(obj, indent=2))


def _version(world: Any, wanted: int | None, default_last: bool = False) -> int:
    if wanted is None:
        return int(world.VERSIONS[-1] if default_last else world.VERSIONS[0])
    if wanted not in world.VERSIONS:
        raise ScenarioError(
            f"the {world.NAME} example has baseline versions {list(world.VERSIONS)}, not {wanted}"
        )
    return wanted


def _scenarios() -> list[Path]:
    try:
        return sorted((examples_dir() / "scenarios").glob("*.yaml"))
    except FileNotFoundError:
        return []


def _cmd_list(args: argparse.Namespace) -> int:
    items = []
    for name in EXAMPLES:
        world = load_world(name)
        dic = world.dictionary(world.VERSIONS[0])
        items.append(
            {
                "example": name,
                "summary": (world.__doc__ or "").splitlines()[0],
                "baseline_versions": list(world.VERSIONS),
                "subsystems": {s: len(n) for s, n in dic.needs.items()},
            }
        )
    scenarios = [str(p) for p in _scenarios()]
    if args.json:
        _json({"examples": items, "scenarios": scenarios})
        return 0
    for item in items:
        print(f"{item['example']:<10}{item['summary']}")
        print(
            f"{'':<10}baseline versions {item['baseline_versions']}; subsystems: "
            + ", ".join(item["subsystems"])
        )
    if scenarios:
        print("\nscenarios (bridge-island run <file>):")
        print("\n".join(f"  {s}" for s in scenarios))
    print("\nNext: bridge-island demo aviation")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    world = load_world(args.example)
    if args.json:
        from bridge_island.diffing import diff_versions
        from bridge_island.summary import summarize

        versions = []
        for v in world.VERSIONS:
            system = world.build(v, args.seed, rate_policy=args.rate_policy)
            system.run(6.0)
            versions.append({"baseline_version": v, "summary": summarize(system)})
        change = diff_versions(world, world.VERSIONS[0], world.VERSIONS[-1])
        _json(
            {
                "example": args.example,
                "seed": args.seed,
                "rate_policy": args.rate_policy,
                "note": "synthetic, illustrative",
                "versions": versions,
                "change": change,
            }
        )
        return 0
    from bridge_island.demo import run_demo

    run_demo(world, args.seed, rate_policy=args.rate_policy)
    print(
        f"\nNext: bridge-island describe {args.example} --version 2 | bridge-island diff {args.example}"
        f" | bridge-island report {args.example}"
    )
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    from bridge_island import describe as dsc

    world = load_world(args.example)
    d = dsc.describe(world, _version(world, args.iface_version))
    if args.format == "json":
        _json(d)
    else:
        print(
            {
                "text": dsc.render_text,
                "markdown": dsc.render_markdown,
                "mermaid": dsc.render_mermaid,
            }[args.format](d)
        )
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    from bridge_island import diffing

    world = load_world(args.example)
    old = _version(world, args.old)
    new = _version(world, args.new, default_last=True)
    if old == new:
        raise ScenarioError("--from and --to are the same version; there is nothing to compare")
    d = diffing.diff_versions(world, old, new)
    if args.format == "json":
        _json(d)
    else:
        print(diffing.render_text(d, args.full))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from bridge_island import scenario as sc
    from bridge_island import summary as sm

    scenario = sc.load_scenario(args.scenario)
    world = load_world(scenario.example)
    system, version = sc.run_scenario(scenario, world)
    summary = sm.summarize(system)
    man = sc.manifest(scenario, world, version)
    trace = sm.trace_rows(system)
    if args.trace:
        sm.write_trace_csv(args.trace, trace)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (args.out / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
        sm.write_trace_csv(args.out / "trace.csv", trace)
    for path in (args.trace, args.out):  # messages go to stderr, never into JSON on stdout
        if path:
            print(f"wrote {path}", file=sys.stderr)
    if args.json:
        _json({"manifest": man, "summary": summary})
        return 0
    print(
        f"scenario {scenario.name}: {scenario.example} baseline v{version}, {scenario.duration_s:g} s, seed {scenario.seed}"
        " (synthetic, illustrative)"
    )
    if scenario.description:
        print(scenario.description)
    print(sm.render_text(summary))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from bridge_island.report import build_report

    stall = None
    if args.stall:
        try:
            at, duration = (float(x) for x in args.stall.split(","))
        except ValueError:
            raise ScenarioError(
                "--stall expects AT,DURATION in seconds, for example 2.2,1.5"
            ) from None
        stall = (at, duration)
    world = load_world(args.example)
    page = build_report(world, args.seed, args.rate_policy, args.duration, stall)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out} ({len(page) // 1024} KiB, self-contained: open it in any browser)")
    return 0


def _cmd_codegen(args: argparse.Namespace) -> int:
    from bridge_island.codegen import CodegenError, dictionary_for_spec, generate_file

    try:
        path = generate_file(args.spec, dictionary_for_spec(args.spec), args.out)
    except CodegenError as exc:
        paint_on = color_enabled(sys.stderr, args.no_color)
        print(paint("codegen failed", "fail", paint_on) + f": {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


def _cmd_experiment(args: argparse.Namespace) -> int:
    from bridge_island import experiments

    experiments.run(args.name, load_world(args.example), args.seed, args.out, args.trials)
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    from bridge_island.ui import serve

    try:
        serve(args.port, not args.no_browser)
    except OSError as exc:
        raise ScenarioError(f"the app could not start: {exc}") from exc
    return 0


def _cmd_check_rules(args: argparse.Namespace) -> int:
    from bridge_island.rules import check_rules

    world = load_world(args.example)
    results = check_rules(world, _version(world, args.iface_version))
    failed = [r for r in results if not r.passed]
    if args.json:
        _json(
            {"example": args.example, "results": [vars(r) for r in results], "passed": not failed}
        )
        return 1 if failed else 0
    on = color_enabled(sys.stdout, args.no_color)
    for r in results:
        mark = paint("PASS", "pass", on) if r.passed else paint("FAIL", "fail", on)
        print(f"{mark}  {r.rule} {r.title}: {r.detail}")
    print(f"{len(results) - len(failed)}/{len(results)} rules pass on the {args.example} example")
    return 1 if failed else 0


_COMMANDS = {
    "list": _cmd_list,
    "demo": _cmd_demo,
    "describe": _cmd_describe,
    "diff": _cmd_diff,
    "run": _cmd_run,
    "report": _cmd_report,
    "codegen": _cmd_codegen,
    "experiment": _cmd_experiment,
    "check-rules": _cmd_check_rules,
    "ui": _cmd_ui,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (ScenarioError, FileNotFoundError, yaml.YAMLError) as exc:
        on = color_enabled(sys.stderr, args.no_color)
        print(paint("error", "fail", on) + f": {exc}", file=sys.stderr)
        if isinstance(exc, ScenarioError):
            print(
                "hint: see docs (Command-line reference, Scenarios) or `bridge-island list`.",
                file=sys.stderr,
            )
        return 2


if __name__ == "__main__":
    sys.exit(main())
