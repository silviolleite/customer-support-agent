"""The web front end: FastAPI on one port, serving the SPEC §7.2 routes and the page at /.

It holds no pipeline logic (P-1): /api/chat streams the very generator the CLI consumes, as
application/x-ndjson, so both front ends emit identical events (W-2).

    .venv/bin/python -m support.web        # started by ./run.sh, port 8000
"""

import asyncio
import json
import os
import socket
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from guards.a2a_client import JUDGE_URL, MASKER_URL
from guards.sanitizer import MAX_CHARS
from support import telemetry
from support.auth import login
from support.config import MODEL, TOOLBOX_URL
from support.pipeline import Session, open_session, run_turn

HOST = "127.0.0.1"
PORT = int(os.environ.get("WEB_PORT", "8000"))
PAGE = Path(__file__).parent / "static" / "index.html"

app = FastAPI(title="Customer Support")
# user_id -> their session (one agent per logged-in customer, stage 3) and a lock, so two
# tabs of the same customer take turns instead of interleaving one ADK session.
_sessions: dict[str, tuple[Session, asyncio.Lock]] = {}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.get("/")
async def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


@app.post("/api/login")
async def api_login(request: Request) -> JSONResponse:
    body = await request.json()
    profile = await login(str(body.get("email", "")).strip(), str(body.get("password", "")))
    if profile is None:
        return _error(401, "Invalid email or password.")
    user_id = profile["email"]
    if user_id not in _sessions:
        _sessions[user_id] = (await open_session(profile), asyncio.Lock())
    return JSONResponse({"user_id": user_id, "full_name": profile["full_name"],
                         "is_premium": profile["is_premium_customer"]})


@app.post("/api/logout")
async def api_logout(request: Request) -> JSONResponse:
    body = await request.json()
    if entry := _sessions.pop(str(body.get("user_id", "")), None):
        await entry[0].close()
    return JSONResponse({"ok": True})


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    message = str(body.get("message", ""))
    entry = _sessions.get(str(body.get("user_id", "")))
    # Status set fixed by §7.2: these three are refused before any streaming starts.
    if entry is None:
        return _error(401, "Not logged in.")
    if not message.strip():
        return _error(400, "message is empty")
    if len(message) > MAX_CHARS:
        return _error(413, "message too long")
    session, lock = entry

    async def stream():
        # The agent.turn span is opened inside run_turn, i.e. inside this generator that the
        # response iterates, so the whole turn is one trace (TECHNICAL troubleshooting).
        async with lock:
            async for event in run_turn(session, message):
                yield json.dumps(event, default=str) + "\n"
        telemetry.flush()

    return StreamingResponse(stream(), media_type="application/x-ndjson")


async def _check_http(client: httpx.AsyncClient, url: str, **kwargs) -> str:
    try:
        response = await client.get(url, **kwargs)
        return "ok" if response.status_code == 200 else f"HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        return f"unreachable: {exc.__class__.__name__}"


def _check_db() -> str:
    """Postgres accepts connections (a TCP check: no SQL in Python, M-2)."""
    try:
        with socket.create_connection(("127.0.0.1", int(os.environ.get("POSTGRES_PORT", "5433"))), timeout=2):
            return "ok"
    except OSError as exc:
        return f"unreachable: {exc}"


@app.get("/health")
async def health() -> JSONResponse:
    async with httpx.AsyncClient(timeout=3) as client:
        toolbox, judge, masker, mem0, phoenix = await asyncio.gather(
            _check_http(client, f"{TOOLBOX_URL}/api/toolset/support_agent"),
            _check_http(client, f"{JUDGE_URL}/.well-known/agent-card.json"),
            _check_http(client, f"{MASKER_URL}/.well-known/agent-card.json"),
            _check_http(client, "https://api.mem0.ai/v1/ping/",
                        headers={"Authorization": f"Token {os.environ.get('MEM0_API_KEY', '')}"}),
            _check_http(client, f"{telemetry.PHOENIX_URL}/healthz"),
        )
    checks = {"db": _check_db(), "toolbox": toolbox, "judge": judge, "masker": masker,
              "mem0": mem0, "phoenix": phoenix}
    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse({"status": "ok" if healthy else "degraded", "model": MODEL, **checks},
                        status_code=200 if healthy else 503)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
