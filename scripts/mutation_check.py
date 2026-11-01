"""Mutation testing: change the code on purpose and check that the tests notice.

For each sampled mutation (flip ``<`` to ``<=``, ``+`` to ``-``, ``and`` to ``or``, change a
constant by one, negate a condition ...) the relevant tests run against a mutated copy of the
package. A mutant is *killed* if any test fails, and *survived* if all pass: a survivor is either
behaviour no test pins down or an equivalent change that does not alter behaviour. The score is
killed / total.

Cross-platform and dependency-free (the usual tools need ``fork``). Run it with the project's
interpreter, from the repository root::

    uv run python scripts/mutation_check.py --sample 300 --jobs 4

Output goes to ``outputs/mutation.json``. It takes several minutes: it is a quality check, not
part of the normal test run.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "bridge_island"

BRIDGE_TESTS = [
    "tests/test_bridge_behavior.py",
    "tests/test_guidelines.py",
    "tests/test_aviation_v2.py",
]
TARGETS: dict[str, list[str]] = {
    "core/transforms.py": [
        "tests/test_transforms.py",
        "tests/test_codegen.py",
        "tests/test_units.py",
    ],
    "core/units.py": ["tests/test_units.py", "tests/test_transforms.py"],
    "core/clock.py": ["tests/test_clock.py", "tests/test_protocols.py"],
    "core/links.py": ["tests/test_guidelines.py", "tests/test_p2p.py"],
    "protocols/command_response.py": ["tests/test_protocols.py", "tests/test_guidelines.py"],
    "baseline/platform.py": ["tests/test_guidelines.py", "tests/test_p2p.py"],
    "unique/subsystem.py": ["tests/test_bridge_behavior.py", "tests/test_age_tolerance.py"],
    "bridge/uid.py": [
        "tests/test_rate_policy.py",
        "tests/test_regressions.py",
        "tests/test_bridge_behavior.py",
    ],
    "bridge/health.py": BRIDGE_TESTS,
    "bridge/cyber.py": BRIDGE_TESTS,
    "bridge/oiu.py": BRIDGE_TESTS,
    "bridge/translator.py": BRIDGE_TESTS,
    "bridge/bridge.py": BRIDGE_TESTS,
    "codegen/generate.py": [
        "tests/test_codegen.py",
        "tests/test_guidelines.py",
        "tests/test_tutorial.py",
    ],
    "age.py": ["tests/test_age.py", "tests/test_age_tolerance.py"],
    "analysis/change_impact.py": ["tests/test_analysis.py"],
    "scenario.py": ["tests/test_ui.py"],
}

for _tests in TARGETS.values():
    if "tests/test_mutation_gaps.py" not in _tests:
        _tests.append("tests/test_mutation_gaps.py")

SWAPS: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.LShift: ast.RShift,
    ast.RShift: ast.LShift,
}


class Mutator(ast.NodeTransformer):
    """Applies the mutation at one site (``target``), or just counts the sites (``target=-1``)."""

    def __init__(self, target: int) -> None:
        self.target, self.count, self.done, self.line = target, 0, "", 0

    def _hit(self, node: ast.AST, description: str) -> bool:
        index = self.count
        self.count += 1
        if index == self.target:
            self.done, self.line = description, getattr(node, "lineno", 0)
            return True
        return False

    # annotations are not behaviour
    def visit_arg(self, node: ast.arg) -> ast.arg:
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:
        if node.value is not None:
            node.value = self.visit(node.value)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.body = [self.visit(s) for s in node.body]
        return node

    def visit_Assert(self, node: ast.Assert) -> ast.Assert:
        return node  # asserts only narrow types for the checker

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if len(node.ops) == 1 and type(node.ops[0]) in SWAPS:
            old = type(node.ops[0]).__name__
            if self._hit(node, f"{old} -> {SWAPS[type(node.ops[0])].__name__}"):
                node.ops[0] = SWAPS[type(node.ops[0])]()
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if type(node.op) in SWAPS and self._hit(
            node, f"{type(node.op).__name__} -> {SWAPS[type(node.op)].__name__}"
        ):
            node.op = SWAPS[type(node.op)]()
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        flipped = ast.Or if isinstance(node.op, ast.And) else ast.And
        if self._hit(node, f"{type(node.op).__name__} -> {flipped.__name__}"):
            node.op = flipped()
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._hit(node, "remove 'not'"):
            return node.operand
        return node

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        if self._hit(node, "negate if-condition"):
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        value = node.value
        if isinstance(value, bool):
            if self._hit(node, f"{value} -> {not value}"):
                return ast.copy_location(ast.Constant(not value), node)
        elif isinstance(value, (int, float)) and self._hit(node, f"{value!r} -> {value + 1!r}"):
            return ast.copy_location(ast.Constant(value + 1), node)
        return node


def sites(source: str) -> int:
    mutator = Mutator(-1)
    mutator.visit(ast.parse(source))
    return mutator.count


def mutate(source: str, index: int) -> tuple[str, str, int]:
    tree = ast.parse(source)
    mutator = Mutator(index)
    tree = mutator.visit(tree)
    return ast.unparse(ast.fix_missing_locations(tree)), mutator.done, mutator.line


def run_tests(workdir: Path, tests: list[str], timeout: int) -> str:
    env = {**os.environ, "PYTHONPATH": str(workdir), "HYPOTHESIS_PROFILE": "mutation"}
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-x",
        "-q",
        "-p",
        "no:cacheprovider",
        "--no-header",
        *tests,
    ]
    try:
        done = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "killed"  # a mutant that hangs is caught by the timeout
    return "survived" if done.returncode == 0 else "killed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--target", action="append", help="limit to these files (relative to the package)"
    )
    ap.add_argument("--sample", type=int, default=300, help="how many mutants in total")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=180, help="seconds per mutant")
    ap.add_argument(
        "--only", type=Path, help="re-run only the survivors recorded in this JSON file"
    )
    args = ap.parse_args()
    targets = {t: TARGETS[t] for t in (args.target or TARGETS)}

    rng = random.Random(args.seed)
    plan: list[tuple[str, int]] = []
    for target in targets:
        n = sites((PKG / target).read_text(encoding="utf-8"))
        plan += [(target, i) for i in range(n)]
    if args.only:
        earlier = json.loads(args.only.read_text(encoding="utf-8"))
        sample = sorted((r["file"], r["site"]) for r in earlier if r["outcome"] == "survived")
    else:
        sample = sorted(rng.sample(plan, min(args.sample, len(plan))))
    print(f"{len(plan)} possible mutants in {len(targets)} files; running {len(sample)}")

    base = Path(tempfile.mkdtemp(prefix="bridge-mutation-"))
    workdirs: list[Path] = []
    for j in range(args.jobs):
        shutil.copytree(
            PKG, base / f"w{j}" / "bridge_island", ignore=shutil.ignore_patterns("__pycache__")
        )
        workdirs.append(base / f"w{j}")
    first = next(iter(targets))
    if run_tests(workdirs[0], targets[first], args.timeout) != "survived":
        print("the unmutated code does not pass its tests: aborting")
        return 2

    def job(item: tuple[int, tuple[str, int]]) -> dict[str, object]:
        n, (target, index) = item
        work = workdirs[n % args.jobs]
        original = (PKG / target).read_text(encoding="utf-8")
        mutated, what, line = mutate(original, index)
        path = work / "bridge_island" / target
        path.write_text(mutated, encoding="utf-8")
        try:
            outcome = run_tests(work, targets[target], args.timeout)
        finally:
            path.write_text(original, encoding="utf-8")
        return {
            "file": target,
            "site": index,
            "change": what,
            "line_hint": line,
            "outcome": outcome,
        }

    results: list[dict[str, object]] = []
    # one worker thread per working copy, so two mutants never touch the same copy at once
    buckets: list[list[tuple[int, tuple[str, int]]]] = [[] for _ in range(args.jobs)]
    for n, item in enumerate(sample):
        buckets[n % args.jobs].append((n, item))

    def run_bucket(bucket: list[tuple[int, tuple[str, int]]]) -> list[dict[str, object]]:
        out = []
        for item in bucket:
            out.append(job(item))
            print(".", end="", flush=True)
        return out

    with ThreadPoolExecutor(args.jobs) as pool:
        for part in pool.map(run_bucket, buckets):
            results += part
    print()
    shutil.rmtree(base, ignore_errors=True)

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "mutation.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    report(results)
    return 0


def report(results: list[dict[str, object]]) -> None:
    by_file: dict[str, list[dict[str, object]]] = {}
    for r in results:
        by_file.setdefault(str(r["file"]), []).append(r)
    total_killed = sum(1 for r in results if r["outcome"] == "killed")
    print(f"{'file':<34}{'mutants':>8}{'killed':>8}{'score':>8}")
    for file, rs in sorted(by_file.items()):
        k = sum(1 for r in rs if r["outcome"] == "killed")
        print(f"{file:<34}{len(rs):>8}{k:>8}{100 * k / len(rs):>7.0f}%")
    print(
        f"{'TOTAL':<34}{len(results):>8}{total_killed:>8}{100 * total_killed / len(results):>7.0f}%"
    )
    print("\nSurvivors (tests did not notice):")
    for r in results:
        if r["outcome"] == "survived":
            print(f"  {r['file']} site {r['site']}: {r['change']}")


if __name__ == "__main__":
    sys.exit(main())
