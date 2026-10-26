"""Export GitHub traffic data (14-day window only) to dated CSVs outside the repository.

Needs the GitHub CLI, authenticated with push access to the repository. Run weekly.
Usage: python scripts/export_traffic.py OWNER/REPO [--out ..\\bridge-island-evidence]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
from pathlib import Path

# endpoint -> (key holding the rows in the response, or None if the response is a list)
ENDPOINTS = {
    "views": "views",
    "clones": "clones",
    "popular/referrers": None,
    "popular/paths": None,
}


def gh_api(path: str) -> object:
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", help="OWNER/REPO")
    ap.add_argument("--out", type=Path, default=Path("..") / "bridge-island-evidence")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    for endpoint, key in ENDPOINTS.items():
        data = gh_api(f"repos/{args.repo}/traffic/{endpoint}")
        rows = data[key] if key else data
        assert isinstance(rows, list)
        dest = args.out / f"{today}_{endpoint.replace('/', '_')}.csv"
        fields = sorted({k for r in rows for k in r})
        with dest.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{dest}: {len(rows)} row(s)")


if __name__ == "__main__":
    main()
