"""Command-line entry point (`bridge-island`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from bridge_island import __version__
from bridge_island.examples import EXAMPLES


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bridge-island", description=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run an invented example and compare with point to point")
    demo.add_argument("example", choices=EXAMPLES)
    demo.add_argument("--seed", type=int, default=1)

    gen = sub.add_parser("codegen", help="compile a YAML mapping spec to a Python module (G5)")
    gen.add_argument("--spec", type=Path, required=True)
    gen.add_argument("--out", type=Path, help="default: <spec dir>/generated/<spec name>.py")

    exp = sub.add_parser(
        "experiment", help="run a reproducible experiment, writing CSV (and plots)"
    )
    exp.add_argument("name", choices=["topology", "change-impact", "timing", "containment"])
    exp.add_argument("--seed", type=int, default=1)
    exp.add_argument("--out", type=Path, default=Path("outputs"))
    exp.add_argument("--example", choices=EXAMPLES, default="aviation")
    exp.add_argument("--trials", type=int, default=1000, help="change-impact Monte Carlo trials")

    rules = sub.add_parser("check-rules", help="run the G1-G6 rule checks on an example")
    rules.add_argument("--example", choices=EXAMPLES, default="aviation")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        from bridge_island.demo import run_demo
        from bridge_island.examples import load_world

        run_demo(load_world(args.example), args.seed)
    elif args.command == "codegen":
        from bridge_island.codegen import CodegenError, dictionary_for_spec, generate_file

        try:
            path = generate_file(args.spec, dictionary_for_spec(args.spec), args.out)
        except CodegenError as exc:
            print(f"codegen failed: {exc}", file=sys.stderr)
            return 1
        print(f"wrote {path}")
    elif args.command == "experiment":
        from bridge_island import experiments
        from bridge_island.examples import load_world

        experiments.run(args.name, load_world(args.example), args.seed, args.out, args.trials)
    else:
        from bridge_island.examples import load_world
        from bridge_island.rules import check_rules

        world = load_world(args.example)
        results = check_rules(world)
        for r in results:
            print(f"{'PASS' if r.passed else 'FAIL'}  {r.rule} {r.title}: {r.detail}")
        failed = [r for r in results if not r.passed]
        print(
            f"{len(results) - len(failed)}/{len(results)} rules pass on the {args.example} example"
        )
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
