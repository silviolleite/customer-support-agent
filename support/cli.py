"""The CLI: log in, then run each line through the pipeline and render its events.

    python -m support.cli                                  # interactive
    CS_PASSWORD=alice python -m support.cli --user alice.jones@example.com --events

--events prints the raw event stream as NDJSON on stdout and nothing else (C-3); prompts
and greetings go to stderr. The password comes from CS_PASSWORD when set (stage 4 decision),
otherwise from a hidden prompt (C-1)."""

import argparse
import asyncio
import getpass
import json
import os
import sys

from support import telemetry
from support.auth import login
from support.pipeline import open_session, run_turn

QUIT = {"quit", "exit", "q"}


def _ms(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ms"


def _sql_with_values(info: dict, args: dict) -> str:
    """The tool's statement with each $n replaced by the value it ran with. A bound parameter
    is shown as bound: the model never chose it (§7.1)."""
    sql = info["statement"]
    # Highest $n first, so $1 does not also match the start of $10.
    for n, param in reversed(list(enumerate(info["params"], start=1))):
        if param in info["bound"]:
            value = "<bound: session email>"
        else:
            value = args.get(param)
            value = repr(json.dumps(value) if isinstance(value, dict) else value)
        sql = sql.replace(f"${n}", value)
    return sql


class Renderer:
    """One readable line per event (C-2), in the stage 4 sketch's format:
    [key] status · latency · span - detail"""

    def __init__(self) -> None:
        self.trace_url: str | None = None

    def __call__(self, event: dict) -> None:
        kind = event["type"]
        if kind == "trace":
            self.trace_url = event["url"]
            print(f"[trace] {event['turn_id']}")
        elif kind == "step":
            status = "BLOCKED" if event["status"] == "blocked" else event["status"]
            line = f"[{event['key']}] {status} · {_ms(event['ms'])} · {event['span']}"
            print(line + (f" - {event['detail']}" if event.get("detail") else ""))
            for memory in event.get("memories", []):
                mark = "inserted" if memory["inserted"] else f"skipped ({memory['reason']})"
                print(f"         {memory['score']:.2f} {mark}: {memory['memory']}")
        elif kind == "llm":
            tokens = f"{event['tokens_in']:,}→{event['tokens_out']:,} tokens".replace(",", " ")
            print(f"[llm] {event['decision']} · {_ms(event['ms'])} · {tokens} · {event['span']}")
        elif kind == "tool_call":
            print(f"[tool] call · {event['name']}({json.dumps(event['args'])})")
            if event.get("info"):
                print(f"       {_sql_with_values(event['info'], event['args'])}")
        elif kind == "tool_result":
            result = event["result"]
            if event["ok"]:
                outcome = f"{len(result)} row" + ("" if len(result) == 1 else "s")
            else:
                outcome = f"error: {result}"
            print(f"[tool] result · {_ms(event['ms'])} · {outcome}")
        elif kind == "final":
            print(f"Agent: {event['response']}")
            tokens = f"{event['tokens']['in']:,}→{event['tokens']['out']:,} tokens".replace(",", " ")
            status = f"blocked at {event['blocked_at']}" if event["blocked"] else event["terminated"]
            print(f"[{status}] {_ms(event['wall_clock_ms'])} · {tokens}")
            self._print_trace()
        elif kind == "error":
            print(f"[error] {event['step']} · {event['status']} · {event['error']}")
            self._print_trace()

    def _print_trace(self) -> None:
        # O-5: the trace link after every turn (a real URL from stage 5 on).
        print(f"[trace] {self.trace_url or 'no trace yet (stage 5)'}\n")


def read_message(events_mode: bool) -> str | None:
    """Next line from the user, or None at end of input."""
    try:
        if events_mode or not sys.stdin.isatty():
            line = sys.stdin.readline()
            return line.strip() if line else None
        return input("You: ").strip()
    except EOFError:
        return None


async def main() -> int:
    parser = argparse.ArgumentParser(description="Customer support CLI")
    parser.add_argument("--user", help="email to log in as (otherwise prompted)")
    parser.add_argument("--events", action="store_true", help="print raw NDJSON events only")
    args = parser.parse_args()

    if args.user:
        email = args.user
    else:
        print("Email: ", end="", file=sys.stderr, flush=True)
        email = sys.stdin.readline().strip()
    password = os.environ.get("CS_PASSWORD") or getpass.getpass("Password: ")
    profile = await login(email, password)
    if profile is None:
        print("Invalid email or password.", file=sys.stderr)
        return 1
    print(f"Hello, {profile['full_name']}. Type quit to leave.", file=sys.stderr)

    session = await open_session(profile)
    render = Renderer()
    try:
        while (message := read_message(args.events)) is not None:
            if message.lower() in QUIT:
                break
            if not message:
                continue
            async for event in run_turn(session, message):
                if args.events:
                    print(json.dumps(event, default=str), flush=True)
                else:
                    render(event)
    finally:
        await session.close()
        telemetry.flush()  # spans are batched; send them before the process exits
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
