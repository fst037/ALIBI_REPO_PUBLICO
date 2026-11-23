"""Shared fixture loader for the in-memory domain stores."""

import json
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_json(name: str):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
