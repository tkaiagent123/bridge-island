"""Experiment 4: a faulty unique system, on the Bridge and on point to point.

Usage: python experiments/fault_containment.py [--seed N] [--out DIR] [--example aviation|medical]
All numbers are synthetic, illustrative.
"""

import sys

from bridge_island.cli import main

if __name__ == "__main__":
    sys.exit(main(["experiment", "containment", *sys.argv[1:]]))
