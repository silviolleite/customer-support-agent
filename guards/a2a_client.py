"""Calls an A2A guard service with `message/send` and returns the text it answered.

Fail loud (P-4, R-4): an unreachable service, a timeout, an HTTP or JSON-RPC error, or a
reply with no text all raise GuardError. Nothing here ever turns a failure into "allow".

    .venv/bin/python -m guards.a2a_client judge "What's the status of order 3?"
"""

import asyncio
import os
import sys
import time
import uuid

import httpx

JUDGE_URL = os.environ.get("JUDGE_URL", "http://127.0.0.1:10002")
TIMEOUT_S = 10.0


class GuardError(Exception):
    """A guard could not give a verdict. The pipeline turns this into an `error` event."""


async def send_message(url: str, text: str, timeout: float = TIMEOUT_S) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {"message": {"role": "user", "kind": "message", "messageId": uuid.uuid4().hex,
                               "parts": [{"kind": "text", "text": text}]}},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{url}/", json=payload)
    except httpx.TimeoutException as exc:
        raise GuardError(f"timed out after {timeout:.0f} s") from exc
    except httpx.HTTPError as exc:
        raise GuardError(f"unreachable: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code != 200:
        raise GuardError(f"HTTP {response.status_code}: {response.text[:200]}")
    body = response.json()
    if "error" in body:
        raise GuardError(f"JSON-RPC error: {body['error'].get('message', body['error'])}")
    result = body.get("result", {})
    texts = [part.get("text", "") for artifact in result.get("artifacts", []) for part in artifact.get("parts", [])]
    text = "".join(texts).strip()
    if not text:
        raise GuardError(f"reply had no text (task state: {result.get('status', {}).get('state')})")
    return text


async def _main(service: str, text: str) -> None:
    url = {"judge": JUDGE_URL}[service]
    started = time.monotonic()
    reply = await send_message(url, text)
    print(f"{reply}\n({int((time.monotonic() - started) * 1000)} ms)")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
