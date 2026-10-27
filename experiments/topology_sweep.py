"""Experiment 1: topology sweep (interface counts, P2P versus Bridge).

Usage: python experiments/topology_sweep.py [--seed N] [--out DIR] [--example aviation|medical]
All numbers are synthetic, illustrative.
"""

import sys

from bridge_island.cli import main

if __name__ == "__main__":
    sys.exit(main(["experiment", "topology", *sys.argv[1:]]))
