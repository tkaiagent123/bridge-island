"""Cassettes: recorded model responses, one JSON object per line, keyed by a hash of the request.

A cassette holds the response text, token counts and latency, never the prompt (its hash is enough
to detect that the prompt code changed) and never a credential.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_KEY_SHAPES = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._\-]{20,}|"
    r"(?:api[_-]?key|x-api-key|authorization)\s*[:=]\s*\S{8,}",
    re.IGNORECASE,
)
_KEY_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")


class CassetteMiss(KeyError):
    """No recorded response for this request."""


def looks_like_secret(text: str) -> bool:
    """True if the text resembles an API key, or contains the value of a key in the environment."""
    if _KEY_SHAPES.search(text):
        return True
    return any(v and len(v) >= 8 and v in text for v in (os.environ.get(k) for k in _KEY_ENV))


def load(path: Path) -> dict[str, dict[str, Any]]:
    """All records in a cassette file, by key. A missing file is an empty cassette."""
    records: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return records
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            records[rec["key"]] = rec
        except (ValueError, KeyError, TypeError):
            raise ValueError(f"{path.name} line {n}: not a cassette record") from None
    return records


def append(path: Path, record: dict[str, Any]) -> None:
    line = json.dumps(record, sort_keys=True, ensure_ascii=False)
    if looks_like_secret(line):
        raise ValueError("refusing to write a cassette record that looks like it holds a key")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
