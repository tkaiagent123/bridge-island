import os
import sys
from pathlib import Path

import pytest
from hypothesis import settings

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples" / "aviation_invented"))

# Used by scripts/mutation_check.py: reproducible, and no shared example database between workers.
settings.register_profile("mutation", database=None, derandomize=True, deadline=None)
if os.environ.get("HYPOTHESIS_PROFILE"):
    settings.load_profile(os.environ["HYPOTHESIS_PROFILE"])


@pytest.fixture
def spec_v1() -> Path:
    return ROOT / "examples" / "aviation_invented" / "mapping_v1.yaml"
