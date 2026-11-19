"""Insulin-loop experiment change-ledger: CSVs to outputs/, figures copied to
outputs/figures/ and docs/images/insulin-loop/.

Usage: python experiments/change_ledger.py [--seed N] [--out DIR]
All numbers are synthetic, illustrative; not medical software.
"""

import shutil
import sys
from pathlib import Path

from bridge_island.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    code = main(["experiment", "change-ledger", "--example", "insulin-loop", *args])
    out = Path(args[args.index("--out") + 1]) if "--out" in args else Path("outputs")
    for target in (out / "figures", Path("docs/images/insulin-loop")):
        target.mkdir(parents=True, exist_ok=True)
        for fig in sorted((out / "change-ledger").glob("fig*.*")):
            shutil.copy2(fig, target / fig.name)
    sys.exit(code)
