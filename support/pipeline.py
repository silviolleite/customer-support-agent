"""One turn of the support desk as a stream of events (SPEC §7.1). The CLI and the web app
only render what this yields (P-1). Each turn is one Phoenix trace under an `agent.turn`
span (O-2, §10). So far: the agent step; the guards and memory arrive in stages 6 to 8."""

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import yaml
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.trace import Status, StatusCode

from support import telemetry

# Before anything builds an agent, so ADK's spans join our traces.
TRACER = telemetry.setup()

from support.agent import AGENT_NAME, build_support_agent
from support.config import MODEL, SESSION_EMAIL_PARAMS
from support.tool_results import rows

APP_NAME = "customer_support"
RUNS_DIR = Path("runs")

# THRESHOLDS §6. A turn that crosses one ends with terminated "cap" (P-6).
MAX_TOOL_CALLS = 6  # T-BUD-TOOLS
MAX_TOKENS = 30_000  # T-BUD-TOKENS
MAX_WALL_S = 30  # T-BUD-WALL


def _load_tool_info() -> dict[str, dict]:
    """The statement and parameters of each tool, read from tools.yaml so the UI can show the
    SQL that ran (§7.1 tool_call.info). Metadata only: no SQL is executed from Python."""
    info = {}
    for doc in yaml.safe_load_all(Path("mcp_toolbox/tools.yaml").read_text()):
        if doc and doc.get("kind") == "tool":
            statement = " ".join(doc["statement"].split())
            info[doc["name"]] = {
                "kind": "MCP",
                "access": "WRITE" if statement.upper().startswith("INSERT") else "READ",
                "statement": statement,
                "params": [p["name"] for p in doc.get("parameters", [])],  # $n order
                "bound": [p["name"] for p in doc.get("parameters", []) if p["name"] in SESSION_EMAIL_PARAMS],
            }
    return info


TOOL_INFO = _load_tool_info()


@dataclass
class Session:
    """A logged-in customer: their own agent, with their email bound into its tools."""

    email: str
    full_name: str
    runner: Runner
    session_id: str
    toolset: object

    async def close(self) -> None:
        await self.toolset.close()


async def open_session(profile: dict) -> Session:
    agent, toolset = build_support_agent(profile["email"], profile["full_name"])
    sessions = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name=APP_NAME, user_id=profile["email"])
    return Session(profile["email"], profile["full_name"], runner, session.id, toolset)


def _decode_args(args: dict) -> dict:
    """The model sends some arguments (action-log's `parameters`) as JSON strings."""
    decoded = {}
    for key, value in (args or {}).items():
        if isinstance(value, str) and value.strip()[:1] in "{[":
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        decoded[key] = value
    return decoded


def _decode_result(response: dict) -> tuple[bool, object]:
    """Unwrap ADK's {"result": "<JSON string>"} into rows; a tool error is ok=False."""
    if "error" in response:
        return False, str(response["error"])
    value = rows(response.get("result"))
    if len(value) == 1 and set(value[0]) == {"error"}:
        return False, str(value[0]["error"])
    return True, value


async def run_turn(session: Session, message: str) -> AsyncIterator[dict]:
    """Run one turn inside its root span. The span is opened here, in the generator the CLI
    and the web response iterate, so ADK's invoke_agent becomes its child (§10)."""
    turn_id = f"turn_{uuid.uuid4().hex[:16]}"
    attributes = {
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.CHAIN.value,
        SpanAttributes.INPUT_VALUE: message,
        SpanAttributes.USER_ID: session.email,
        SpanAttributes.SESSION_ID: session.session_id,
        "turn.id": turn_id,
    }
    with TRACER.start_as_current_span("agent.turn", attributes=attributes) as span:
        trace_id = format(span.get_span_context().trace_id, "032x")
        async for event in _turn(session, message, turn_id, trace_id):
            if event["type"] == "final":
                span.set_attribute(SpanAttributes.OUTPUT_VALUE, event["response"])
            elif event["type"] == "error":
                span.set_status(Status(StatusCode.ERROR, event["error"]))
            yield event


async def _turn(session: Session, message: str, turn_id: str, trace_id: str) -> AsyncIterator[dict]:
    started = time.monotonic()
    ms = lambda since: int((time.monotonic() - since) * 1000)  # noqa: E731
    log = {"turn_id": turn_id, "trace_id": trace_id, "user": session.email, "message": message,
           "terminated": None, "blocked_at": None, "wall_clock_ms": 0,
           "tokens": {"in": 0, "out": 0}, "steps": [], "tool_calls": [], "llm_calls": 0}

    def finish(terminal: dict) -> dict:
        """Write runs/<turn_id>.json, then hand back the terminal event (P-5)."""
        log["terminated"] = terminal["terminated"]
        log["wall_clock_ms"] = ms(started)
        RUNS_DIR.mkdir(exist_ok=True)
        (RUNS_DIR / f"{turn_id}.json").write_text(json.dumps(log, indent=2, default=str))
        return terminal

    yield {"type": "trace", "turn_id": turn_id, "trace_id": trace_id, "url": telemetry.trace_url(trace_id)}

    yield {"type": "stage", "key": "agent", "label": "Support agent"}
    agent_started = last = time.monotonic()
    pending: dict[str, tuple[int, str, float]] = {}  # ADK call id -> (our id, name, start)
    reply, terminated = "", "done"
    content = types.Content(role="user", parts=[types.Part(text=message)])
    events = session.runner.run_async(
        user_id=session.email, session_id=session.session_id, new_message=content
    ).__aiter__()
    try:
        while True:
            remaining = MAX_WALL_S - (time.monotonic() - started)
            # Python 3.12's wait_for awaits in this same task (no new task for a positive
            # timeout), so ADK's span context survives from one event to the next.
            try:
                event = await asyncio.wait_for(events.__anext__(), timeout=max(remaining, 0.01))
            except StopAsyncIteration:
                break
            except TimeoutError:
                terminated = "cap"
                reply = f"Sorry, I had to stop: this took longer than {MAX_WALL_S} seconds."
                break

            calls = event.get_function_calls()
            if event.usage_metadata:
                usage = event.usage_metadata
                tokens_in, tokens_out = usage.prompt_token_count or 0, usage.candidates_token_count or 0
                log["llm_calls"] += 1
                log["tokens"]["in"] += tokens_in
                log["tokens"]["out"] += tokens_out
                yield {"type": "llm", "model": MODEL,
                       "decision": f"call {', '.join(c.name for c in calls)}" if calls else "final answer",
                       "tokens_in": tokens_in, "tokens_out": tokens_out, "ms": ms(last), "span": "call_llm"}

            for call in calls:
                if len(log["tool_calls"]) + len(pending) >= MAX_TOOL_CALLS:
                    terminated = "cap"
                    reply = f"Sorry, I had to stop: this needed more than {MAX_TOOL_CALLS} tool calls."
                    break
                tool_id = len(log["tool_calls"]) + len(pending) + 1
                pending[call.id] = (tool_id, call.name, time.monotonic())
                yield {"type": "tool_call", "id": tool_id, "name": call.name,
                       "args": _decode_args(call.args), "info": TOOL_INFO.get(call.name),
                       "span": f"execute_tool {call.name}"}
            if terminated == "cap":
                break

            for response in event.get_function_responses():
                tool_id, name, call_started = pending.pop(response.id)
                ok, result = _decode_result(response.response or {})
                entry = {"name": name, "ok": ok, "ms": ms(call_started)}
                if not ok:
                    entry["error"] = result
                log["tool_calls"].append(entry)
                yield {"type": "tool_result", "id": tool_id, "name": name, "ok": ok,
                       "ms": entry["ms"], "result": result}

            if log["tokens"]["in"] + log["tokens"]["out"] > MAX_TOKENS:
                terminated = "cap"
                reply = f"Sorry, I had to stop: this turn used more than {MAX_TOKENS} tokens."
                break
            if event.is_final_response() and event.content and event.content.parts:
                reply = "".join(p.text or "" for p in event.content.parts if not p.thought)
            last = time.monotonic()
    except Exception as exc:  # the agent or a tool failed: fail loud (P-4)
        yield finish({"type": "error", "step": "agent", "status": 502,
                      "error": f"Support agent failed: {exc}", "terminated": "error"})
        return
    finally:
        # Cleanup only: after a timeout the runner's generator may already be torn down.
        with contextlib.suppress(Exception):
            await events.aclose()

    # Rule 3 (§7.3): a call cut off by a cap still gets exactly one tool_result.
    for tool_id, name, call_started in pending.values():
        log["tool_calls"].append({"name": name, "ok": False, "ms": ms(call_started), "error": "not run: turn capped"})
        yield {"type": "tool_result", "id": tool_id, "name": name, "ok": False,
               "ms": ms(call_started), "result": "not run: turn capped"}

    calls_made = len(log["tool_calls"])
    step = {"key": "agent", "status": "passed", "ms": ms(agent_started)}
    log["steps"].append(step)
    yield {"type": "step", **step, "kind": "in-process", "span": f"invoke_agent {AGENT_NAME}",
           "detail": f"{log['llm_calls']} llm calls, {calls_made} tool calls"}

    yield finish({"type": "final", "blocked": False, "blocked_at": None, "response": reply,
                  "terminated": terminated, "wall_clock_ms": ms(started), "tokens": log["tokens"]})
