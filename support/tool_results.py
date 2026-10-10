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


def raw_result(tool_response) -> object:
    """What a tool returned, as an after-tool callback sees it: ADK 2.11 passes the tool's raw
    return value (a JSON string from the Toolbox), not yet wrapped in {"result": ...}."""
    if isinstance(tool_response, dict):
        return tool_response.get("result")
    return tool_response
