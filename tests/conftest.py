import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples" / "aviation_invented"))


@pytest.fixture
def spec_v1() -> Path:
    return ROOT / "examples" / "aviation_invented" / "mapping_v1.yaml"
