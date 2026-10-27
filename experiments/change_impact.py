"""Experiment 2: change impact of a baseline interface change, plus a Monte Carlo mode (--trials N).

Usage: python experiments/change_impact.py [--seed N] [--out DIR] [--example aviation|medical]
All numbers are synthetic, illustrative.
"""

import sys

from bridge_island.cli import main

if __name__ == "__main__":
    sys.exit(main(["experiment", "change-impact", *sys.argv[1:]]))
