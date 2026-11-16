"""The insulin-loop side of the command line: run, demo --story, report, experiment, envelope."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bridge_island.scenario import ScenarioError

NOTE = "synthetic, illustrative; not medical software"


def _json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _patient(text: str) -> str:
    if not (
        len(text) == 4 and text.startswith("VP") and text[2:].isdigit() and 1 <= int(text[2:]) <= 30
    ):
        raise ScenarioError(f"--patient must be VP01..VP30, got {text!r}")
    return text


def is_loop_scenario(path: Path) -> bool:
    from bridge_island.yamlutil import YamlError, load_yaml

    try:
        data = load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YamlError):
        return False  # let the classic loader report the problem
    return isinstance(data, dict) and data.get("example") == "insulin-loop"


def run(args: argparse.Namespace) -> int:
    from bridge_island.clinical import experiment as X
    from bridge_island.clinical import scenarios as S

    scenario, patients, archs = S.load(args.scenario)
    indices = list(range(30)) if patients == "all" else [int(patients[2:]) - 1]
    rows, events = X.run_matrix([scenario], archs, indices, scenario.seed)
    summary = X.summarize(rows)
    if args.out:
        from bridge_island.experiments import _write_csv

        _write_csv(args.out / "runs.csv", rows)
        _write_csv(args.out / "summary.csv", summary)
        for name, ev in events.items():
            _write_csv(args.out / f"critical_events_{name}.csv", ev)
        print(f"wrote {args.out}", file=sys.stderr)
    if args.json:
        _json({"scenario": scenario.name, "note": NOTE, "summary": summary, "runs": rows})
        return 0
    print(
        f"scenario {scenario.name}: insulin-loop, {len(indices)} virtual patient(s), seed {scenario.seed} ({NOTE})"
    )
    print(scenario.description)
    for s in summary:
        told = (
            "no fault" if s["detected"] is None else f"told in {s['detected']} of {s['patients']}"
        )
        print(
            f"  {s['architecture']:<16} 70-180 mg/dL median {s['tir_70_180_min_median']:g} min; "
            f"{s['patients_below_54']} below 54; {told}; {s['patients_unsafe_age']} used unflagged stale glucose"
        )
    return 0


def demo(args: argparse.Namespace) -> int:
    from bridge_island.clinical import story

    data = story.story(_patient(args.patient), args.seed)
    if args.json:
        _json(data)
    else:
        story.render(data)
    return 0


def report(args: argparse.Namespace) -> int:
    from bridge_island.clinical import scenarios as S
    from bridge_island.clinical.report import build_report

    if not args.scenario:
        raise ScenarioError(
            f"insulin-loop reports need --scenario, one of {[p.stem for p in S.shipped()]}"
        )
    page = build_report(args.scenario, args.seed, _patient(args.patient), cohort=not args.no_cohort)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out} ({len(page) // 1024} KiB, self-contained: open it in any browser)")
    return 0


def experiment(args: argparse.Namespace) -> int:
    from bridge_island.clinical import experiment as X

    if args.example != "insulin-loop":
        raise ScenarioError(
            f"{args.name} runs on the insulin-loop example: add --example insulin-loop"
        )
    out = args.out / args.name
    if args.name == "closed-loop":
        patients = None if args.patients is None else range(max(1, min(30, args.patients)))
        files = X.closed_loop(out, args.seed, patients=patients, workers=args.workers)
    else:
        files = X.change_ledger(out, args.seed)
    for path in files.values():
        print(f"wrote {path}")
    return 0


def envelope(args: argparse.Namespace) -> int:
    from bridge_island.clinical import envelope as Env

    if args.envelope_command == "check":
        decision = Env.check(Env.load_change(args.change))
        if args.json:
            _json(decision.as_dict())
            return 0
        d = decision
        print(
            f"{d.change.name}: {'INSIDE' if d.inside else 'OUTSIDE'} the change envelope ({NOTE})"
        )
        for reason in d.reasons:
            print(f"  why: {reason}")
        print("organizations to involve:")
        for org, why in d.owners:
            print(f"  {org}: {why}")
        if d.tests:
            print(f"conformance tests to re-run ({len(d.tests)}), all on the Bridge:")
            for t in d.tests:
                print(f"  {t}")
            print(
                "run them: bridge-island envelope verify --example insulin-loop --change "
                + str(args.change)
            )
        return 0
    names, devices = None, "default"
    if args.change:
        change = Env.load_change(args.change)
        decision = Env.check(change)
        if not decision.inside:
            raise ScenarioError(
                f"{change.name} is outside the envelope: there is no Bridge-only test set to run"
            )
        names, devices = decision.tests, change.simulate
    results = Env.verify(names, devices)
    failed = [n for n, p in results if p]
    if args.json:
        _json(
            {
                "devices": devices,
                "results": [{"test": n, "passed": p is None, "problem": p} for n, p in results],
            }
        )
    else:
        for name, problem in results:
            print(
                f"{'PASS' if problem is None else 'FAIL'}  {name}"
                + (f": {problem}" if problem else "")
            )
        print(
            f"{len(results) - len(failed)}/{len(results)} conformance tests pass on devices '{devices}' ({NOTE})"
        )
    return 1 if failed else 0


def list_lines() -> list[str]:
    from bridge_island.clinical import scenarios as S

    return [str(p) for p in S.shipped()]
