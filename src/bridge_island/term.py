"""Terminal colour that follows the usual conventions (NO_COLOR, TERM=dumb, not a terminal)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TextIO

_CODES = {"pass": "32", "fail": "31", "warn": "33", "dim": "2", "bold": "1"}


def color_enabled(
    stream: TextIO, no_color_flag: bool = False, env: Mapping[str, str] | None = None
) -> bool:
    env = os.environ if env is None else env
    if no_color_flag or env.get("NO_COLOR") or env.get("TERM") == "dumb":
        return False
    if env.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, kind: str, enabled: bool) -> str:
    return f"\x1b[{_CODES[kind]}m{text}\x1b[0m" if enabled else text
