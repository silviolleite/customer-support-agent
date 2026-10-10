"""Run one eval turn through a front end and return its event stream.

Every item is one turn in a fresh session (EVALS §3): log in, send, read to the terminal
event, log out. A fresh session matters: in stage 8 a recall that inserted nothing still got
the right answer from the previous turn in the same session, and hid the miss.

Passwords are the seed's first names (EVALS §2: "Every user's password is their first name").
"""

import json
import os
import subprocess
import sys

import httpx

WEB_URL = os.environ.get("WEB_URL", "http://127.0.0.1:8000")
TURN_TIMEOUT_S = 60  # well above T-BUD-WALL, so a slow turn ends by the pipeline's own cap


def password_for(user: str) -> str:
    return user.lower()


async def web_turn(client: httpx.AsyncClient, email: str, user: str, message: str) -> list[dict]:
    """One turn through POST /api/chat, the stream the browser renders (W-2)."""
    login = await client.post(f"{WEB_URL}/api/login", json={"email": email, "password": password_for(user)})
    login.raise_for_status()
    events = []
    try:
        async with client.stream("POST", f"{WEB_URL}/api/chat", json={"user_id": email, "message": message},
                                 timeout=TURN_TIMEOUT_S) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode()
                return [{"type": "http_error", "status": response.status_code, "body": body}]
            async for line in response.aiter_lines():
                if line.strip():
                    events.append(json.loads(line))
    finally:
        await client.post(f"{WEB_URL}/api/logout", json={"user_id": email})
    return events


def cli_turn(email: str, user: str, message: str) -> list[dict]:
    """One turn through `python -m support.cli --events` (C-3): stdout must be NDJSON only."""
    result = subprocess.run(
        [sys.executable, "-m", "support.cli", "--user", email, "--events"],
        input=message + "\n", capture_output=True, text=True, timeout=TURN_TIMEOUT_S,
        env={**os.environ, "CS_PASSWORD": password_for(user)},
    )
    events = []
    for line in result.stdout.splitlines():
        if line.strip():
            events.append(json.loads(line))  # a non-JSON line on stdout breaks C-3: let it raise
    return events
