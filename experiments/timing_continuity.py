"""Experiment 3: time-tag integrity (G3) and Bridge stall-and-resume.

Usage: python experiments/timing_continuity.py [--seed N] [--out DIR] [--example aviation|medical]
All numbers are synthetic, illustrative.
"""

import sys

from bridge_island.cli import main

if __name__ == "__main__":
    sys.exit(main(["experiment", "timing", *sys.argv[1:]]))
