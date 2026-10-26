"""Command-line entry point (`bridge-island`)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from bridge_island import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bridge-island", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
