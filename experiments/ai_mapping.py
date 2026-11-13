"""The fifth experiment from a script: `python experiments/ai_mapping.py [--example medical]`.

Same as `bridge-island experiment ai-mapping`; replay mode, so it needs no network and no key.
"""

import sys

from bridge_island.cli import main

if __name__ == "__main__":
    sys.exit(main(["experiment", "ai-mapping", *sys.argv[1:]]))
