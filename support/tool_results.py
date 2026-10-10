"""Decode what a Toolbox tool returns into a list of rows.

Over MCP a postgres-sql tool returns a JSON string shaped by its row count: a list for
several rows, a bare object for one, and `null` for none. Callers always get a list."""

import json
from typing import Any


def rows(raw: str | Any) -> list[dict]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)
