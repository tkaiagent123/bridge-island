import os
import socket
import sys
from collections.abc import Iterator
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


_LOCAL = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True, scope="session")
def _no_outbound_network() -> Iterator[None]:
    """No test may reach beyond this machine. Loopback (the app's own server) is allowed."""
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def local_only(address: object) -> None:
        if isinstance(address, tuple) and address[0] not in _LOCAL:
            raise RuntimeError(f"network access is blocked in tests: {address!r}")

    def connect(self: socket.socket, address: object) -> None:
        local_only(address)
        real_connect(self, address)  # type: ignore[arg-type]

    def connect_ex(self: socket.socket, address: object) -> int:
        local_only(address)
        return real_connect_ex(self, address)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", connect)
        mp.setattr(socket.socket, "connect_ex", connect_ex)
        yield
