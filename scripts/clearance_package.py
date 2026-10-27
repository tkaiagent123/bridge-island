"""Write CLEARANCE_PACKAGE.md: a short summary for the release reviewer.

The package lists every tracked file, shows the live release-check and test results, and
confirms the invented-content rules. It is git-ignored and excluded from the public release.
Optional reviewer notes are appended from ``clearance_notes.local.md`` (also git-ignored).

Usage: python scripts/clearance_package.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "CLEARANCE_PACKAGE.md"
NOTES = ROOT / "clearance_notes.local.md"


def run(*cmd: str) -> str:
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()


def tracked_files() -> list[str]:
    return sorted(run("git", "ls-files").splitlines())


def listing(files: list[str]) -> str:
    groups: dict[str, list[str]] = {}
    for f in files:
        groups.setdefault(str(PurePosixPath(f).parent), []).append(PurePosixPath(f).name)
    return "\n".join(
        f"* `{d}/`: " + ", ".join(f"`{n}`" for n in sorted(names))
        for d, names in sorted(groups.items())
    )


def main() -> int:
    files = tracked_files()
    digest = hashlib.sha256("\n".join(files).encode()).hexdigest()[:16]
    commit = run("git", "rev-parse", "--short", "HEAD")
    release = run(sys.executable, "scripts/release_check.py")
    tests = run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider").splitlines()[-1]
    lint = run(sys.executable, "-m", "ruff", "check", ".").splitlines()[-1]
    types = run(sys.executable, "-m", "mypy").splitlines()[-1]
    remotes = run("git", "remote", "-v") or "none"
    text = f"""# Clearance package: bridge-island

Prepared {dt.date.today().isoformat()} at commit `{commit}` (tracked-file list digest `{digest}`).
Not part of the public release. Remotes configured: {remotes}. Nothing has been pushed.

## 1. What is being released

A small Python reference implementation of a bridge/island integration architecture, with two
invented example platforms (aviation, medical device), a point-to-point comparison, four
reproducible experiments, tests and documentation. Apache-2.0.

## 2. Confirmation that all data is invented

* Every platform, subsystem, message, field, rate and number is invented and generic. The
  example platforms are named "Generic Host Air Vehicle" and "Generic Infusion Host Platform".
* The repository contains no real interface control documents, message sets, bus addresses or
  data rates tied to a real platform, and no program metrics of any kind (no schedule
  durations, percentages, effort figures or defect statistics).
* The bus models are generic, simplified behavioral models of publicly known concepts
  (command/response, label-coded words, publish/subscribe). Terms such as "bus controller",
  "remote terminal", "subaddress", "minor frame" and "major frame" are used in their generic
  public sense. Reviewer: please confirm this is acceptable.
* All experiment outputs are labelled "synthetic, illustrative".
* The only real names are in the bibliography (`docs/related-work.md`), cited as published.

## 3. How this was checked

Automated: `scripts/release_check.py` scans every tracked or not-yet-ignored file against
(a) a tracked list of generic patterns and (b) a private, git-ignored list of real aircraft,
program, supplier and product names that the author maintains. It runs in CI (generic list
only) and as a pre-commit hook (both lists).

Manual: the author must review all content before release. `docs/AI_USAGE.md` records which
parts were generated with AI assistance and whether the author has reviewed them.

### Release check output (this run)

```text
{release}
```

### Test run (this run)

```text
{tests}
```

### Lint and types (this run)

```text
ruff: {lint}
mypy: {types}
```

Not re-run by this script: the documentation build (`mkdocs build --strict`) and the coverage
gate (90 percent on `core`, `bridge` and `codegen`). Both passed on a clean clone of the
release commit; CI runs both.

## 4. Excluded from the public release

`CLEARANCE_PACKAGE.md`, `release_check.local.toml`, `clearance_notes.local.md`,
`RELEASE_CHECKLIST.md`, and the `outputs/` and `site/` build directories (all git-ignored).

## 5. Every tracked file ({len(files)})

{listing(files)}
"""
    if NOTES.exists():
        text += "\n## 6. Reviewer notes\n\n" + NOTES.read_text(encoding="utf-8").strip() + "\n"
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(files)} tracked files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
