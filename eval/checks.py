"""Arithmetic checks over event streams, run logs and the seed. No model decides a pass
(EVALS §1, law 2): every function here compares values."""

import re
from pathlib import Path

from eval.extract_gold import seed_emails

STAGE_ORDER = ["sanitize", "judge", "guardrail", "recall", "agent", "mask", "save"]  # P-2
TERMINAL = {"final", "error"}


def terminal(events: list[dict]) -> dict:
    return events[-1] if events and events[-1].get("type") in TERMINAL else {}


def event_order_violations(events: list[dict]) -> list[str]:
    """Every SPEC §7.3 ordering rule this stream breaks (empty list = obeys all six)."""
    problems = []
    if not events or events[0].get("type") != "trace":
        problems.append("rule 1: first event is not trace")
    terminals = [i for i, e in enumerate(events) if e.get("type") in TERMINAL]
    if len(terminals) != 1 or terminals[0] != len(events) - 1:
        problems.append("rule 5: not exactly one terminal event, last")
    open_stage, seen, inside_agent = None, [], False
    calls, results = {}, {}
    for i, event in enumerate(events):
        kind = event.get("type")
        if kind == "stage":
            if open_stage is not None:
                problems.append(f"rule 2: stage {event['key']} began before step {open_stage}")
            if seen and STAGE_ORDER.index(event["key"]) <= STAGE_ORDER.index(seen[-1]):
                problems.append(f"rule 2: stage {event['key']} out of P-2 order")
            open_stage, inside_agent = event["key"], event["key"] == "agent"
            seen.append(event["key"])
        elif kind == "step":
            if event.get("key") != open_stage:
                problems.append(f"rule 2: step {event.get('key')} without its stage")
            if event.get("status") == "blocked":
                nxt = events[i + 1] if i + 1 < len(events) else {}
                if not (nxt.get("type") == "final" and nxt.get("blocked") and nxt.get("blocked_at") == event["key"]):
                    problems.append("rule 4: blocked step not followed by final {blocked, same blocked_at}")
            open_stage, inside_agent = None, False
        elif kind in ("llm", "tool_call", "tool_result"):
            if not inside_agent:
                problems.append(f"rule 2: {kind} outside the agent stage")
            if kind == "tool_call":
                calls[event["id"]] = calls.get(event["id"], 0) + 1
            elif kind == "tool_result":
                results[event["id"]] = results.get(event["id"], 0) + 1
    if any(results.get(call_id) != 1 for call_id in calls) or set(results) - set(calls):
        problems.append("rule 3: a tool_call without exactly one matching tool_result")
    end = terminal(events)
    if (end.get("type") == "error" or end.get("blocked")) and "save" in seen:
        problems.append("rule 6: save on a blocked or error turn")
    return problems


def normalize(text: str) -> str:
    """EVALS §3 matching: case-insensitive, with $ and , removed from both sides."""
    return re.sub(r"[$,]", "", str(text)).lower()


def contains(text: str, expected: str) -> bool:
    return normalize(expected) in normalize(text)


def order_owners() -> dict[int, str]:
    """order_id -> owner's email, from the order table in EVALS §2."""
    text = Path("EVALS.md").read_text()
    emails = seed_emails(text)
    owners = {}
    for order_id, first_name in re.findall(r"^\| (\d+) \| ([a-z]+) \|", text, flags=re.M):
        owners[int(order_id)] = emails[first_name]
    return owners


def tool_calls(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "tool_call"]


def tool_results(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "tool_result"]


def run_log_violations(log: dict) -> list[str]:
    """Gate 3 (EVALS §5): A1 failed tool calls carry an error; A2 no turn ended cap or error;
    B1/B2 the per-turn budgets of THRESHOLDS §6 hold."""
    problems = []
    for call in log.get("tool_calls", []):
        if not call.get("ok") and not call.get("error"):
            problems.append(f"A1: failed tool call {call.get('name')} has no error")
    if log.get("terminated") in ("cap", "error"):
        problems.append(f"A2: turn ended {log['terminated']}")
    if len(log.get("tool_calls", [])) > 6:
        problems.append("B1: more than T-BUD-TOOLS (6) tool calls")
    tokens = log.get("tokens", {})
    if tokens.get("in", 0) + tokens.get("out", 0) > 30_000:
        problems.append("B2: more than T-BUD-TOKENS (30 000) tokens")
    if log.get("wall_clock_ms", 0) > 30_000:
        problems.append("B2: longer than T-BUD-WALL (30 s)")
    return problems
