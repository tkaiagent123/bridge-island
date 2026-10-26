"""Fail if any tracked (or not-yet-ignored) file contains a banned term.

Configuration: ``release_check.toml`` (tracked, generic patterns only) plus an optional
``release_check.local.toml`` (git-ignored, holds real names). Keys in either file:

    exclude  = ["glob", ...]   # files not scanned (tracked file only)
    terms    = ["literal", ...]  # whole-word, case-insensitive
    patterns = ["regex", ...]    # case-insensitive regular expressions

Without the local file the check still runs on the generic list; it fails locally
(not in CI) so a missing private list is noticed before a release.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG = "release_check.toml"
LOCAL_CONFIG = "release_check.local.toml"


def load(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def files_to_scan(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(set(out.splitlines()))


def compile_rules(cfg: dict) -> list[re.Pattern[str]]:
    rules = [re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.I) for t in cfg.get("terms", [])]
    rules += [re.compile(p, re.I) for p in cfg.get("patterns", [])]
    return rules


def scan(root: Path, strict_local: bool = False) -> list[str]:
    """Return a list of ``path:line: rule`` hits (the matched text is not echoed)."""
    cfg = load(root / CONFIG)
    local = root / LOCAL_CONFIG
    merged = {k: list(cfg.get(k, [])) for k in ("terms", "patterns")}
    if local.exists():
        for k in merged:
            merged[k] += load(local).get(k, [])
    elif strict_local:
        return [f"{LOCAL_CONFIG}: missing (private banned-term list not found)"]
    else:
        print(f"note: {LOCAL_CONFIG} not found; scanning with the generic list only")
    rules = compile_rules(merged)
    exclude = cfg.get("exclude", [])
    hits = []
    for name in files_to_scan(root):
        if any(fnmatch.fnmatch(name, g) for g in exclude) or not (root / name).is_file():
            continue
        data = (root / name).read_bytes()
        if b"\0" in data:
            continue
        for n, line in enumerate(data.decode("utf-8", "ignore").splitlines(), 1):
            for rule in rules:
                if rule.search(line):
                    hits.append(f"{name}:{n}: matches banned rule #{rules.index(rule)}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args()
    hits = scan(args.root, strict_local=not os.environ.get("CI"))
    for h in hits:
        print(h)
    print(f"release_check: {len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
