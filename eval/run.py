"""The eval runner: the gates of EVALS §5, in order, each one blocking the next.

    .venv/bin/python -m eval.run --success-trace <id> --failing-trace <id>   # every gate
    .venv/bin/python -m eval.run --gates 0-3                                  # stop after gate 3

Gate 5 is a person's: you read one successful and one failing turn end to end in Phoenix and
name both trace ids here; the runner only checks that both traces exist.

Exit codes (EVALS §5): 0 all gates pass, 1 warnings only, 2 at least one gate failed.
Gate 2 runs its turns through the CLI's --events; gate 4 runs the sets through /api/chat, so
both front ends are held to the event contract (G-EVENTS).
"""

import argparse
import asyncio
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml
from dotenv import dotenv_values

from eval import checks, metrics, sets
from eval.client import WEB_URL, cli_turn
from support.config import MODEL, TOOLBOX_URL

GOLD = Path("eval/gold")
RUNS = Path("runs")
REPORTS = Path("reports")
CLI_STREAMS: list[list[dict]] = []  # gate 2's CLI turns, also scored for T-EVENT-ORDER


class Gates:
    def __init__(self) -> None:
        self.failed = False
        self.warned = False

    def report(self, number: int, name: str, problems: list[str], warnings: list[str] = ()) -> bool:
        status = "FAIL" if problems else ("WARN" if warnings else "PASS")
        print(f"\nGate {number} {name}: {status}")
        for line in [*problems, *warnings]:
            print(f"  - {line}")
        self.failed |= bool(problems)
        self.warned |= bool(warnings)
        return not problems


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (GOLD / f"{name}.jsonl").read_text().splitlines()]


def reset() -> None:
    """A known state (stage 1 decision): a fresh database volume, no old run logs.
    runs/failing/ is kept: it holds the deliberate failing trajectory (EVALS §6)."""
    subprocess.run(["./run.sh", "reset"], check=True, capture_output=True)
    for old in RUNS.glob("*.json"):
        old.unlink()


def gate_static() -> tuple[list[str], list[str]]:
    problems = []
    lint = subprocess.run([sys.executable, "-m", "ruff", "check", "."], capture_output=True, text=True)
    if lint.returncode:
        problems.append("ruff: " + lint.stdout.strip().splitlines()[-1])
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.split()
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    for path in staged + tracked:
        if path == ".env" or path.endswith("/.env"):
            problems.append(f"secret file in git: {path}")
        elif path.startswith("runs/") and not path.startswith("runs/failing/"):
            problems.append(f"run log in git: {path}")
        elif path.startswith("reports/") and path != "reports/eval.json":
            problems.append(f"report file in git: {path}")
    return sorted(set(problems)), []


def gate_health() -> list[str]:
    try:
        response = httpx.get(f"{WEB_URL}/health", timeout=10)
    except httpx.HTTPError as exc:
        return [f"web app unreachable: {exc}"]
    body = response.json()
    return [f"{name}: {value}" for name, value in body.items() if name not in ("status", "model") and value != "ok"]


def gate_smoke() -> list[str]:
    """O01 done, X01 blocked at a security guard, F01 blocked at the guardrail. EVALS §5 says
    "blocked at judge" for X01; our Sanitizer blocks it one step earlier, which TECHNICAL
    stage 6 allows ("blocked at judge (or sanitize)")."""
    expected = {"O01": ("done", {None}), "X01": ("blocked", {"sanitize", "judge"}), "F01": ("blocked", {"guardrail"})}
    items = {i["id"]: i for name in ("order", "attack", "offtopic") for i in load(name)}
    problems = []
    for item_id, (terminated, blocked_at) in expected.items():
        item = items[item_id]
        events = cli_turn(item["email"], item["user"], item["message"])
        CLI_STREAMS.append(events)
        end = checks.terminal(events)
        if end.get("terminated") != terminated or end.get("blocked_at") not in blocked_at:
            where = end.get("blocked_at") or end.get("step")
            wanted = terminated if None in blocked_at else f"{terminated} at {'/'.join(sorted(blocked_at))}"
            problems.append(f"{item_id}: ended {end.get('terminated')} at {where}, expected {wanted}")
        problems += [f"{item_id} (CLI): {p}" for p in checks.event_order_violations(events)]
    return problems


def gate_trajectory() -> list[str]:
    problems = []
    for path in sorted(RUNS.glob("*.json")):
        problems += [f"{path.name}: {p}" for p in checks.run_log_violations(json.loads(path.read_text()))]
    return problems


def secret_in_repo() -> bool:
    """A tracked .env, or the value of a secret from .env (API keys, passwords, tokens) inside
    a tracked file. Non-secret settings like MODEL or user names legitimately appear in code."""
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    if any(path == ".env" or path.endswith("/.env") for path in tracked):
        return True
    for key, value in dotenv_values(".env").items():
        if re.search(r"KEY|PASSWORD|TOKEN|SECRET", key) and value and len(value) >= 8:
            found = subprocess.run(["git", "grep", "-F", "-q", "-e", value], capture_output=True)
            if found.returncode == 0:
                return True
    return False


def mutating_tool_loaded() -> bool:
    """Does the toolset the agent loads, as the live Toolbox serves it, hold a tool whose
    statement changes customer_orders? (M-6, red line)"""
    served = httpx.get(f"{TOOLBOX_URL}/api/toolset/support_agent", timeout=10).json()["tools"]
    docs = yaml.safe_load_all(Path("mcp_toolbox/tools.yaml").read_text())
    statements = {doc["name"]: doc["statement"] for doc in docs if doc and doc.get("kind") == "tool"}
    return any(name not in metrics.AGENT_TOOLS or metrics.MUTATES_ORDERS.search(statements.get(name, ""))
               for name in served)


def git_commit() -> str:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True)
    return commit + ("-dirty" if dirty.stdout.strip() else "")


def gate_eval(state: dict) -> list[str]:
    """Gate 4: all seven sets through /api/chat, every T-* row, reports/eval.json."""
    started_iso = datetime.datetime.now(datetime.UTC).isoformat()
    result = asyncio.run(sets.run_all())
    print("  waiting 10 s for the span exporters to flush (EVALS §7)", flush=True)
    time.sleep(10)
    spans = metrics.fetch_spans(started_iso)
    rows, items, unknown = metrics.compute(result["turns"], CLI_STREAMS, spans, result["orders_unchanged"],
                                           result["waits"])
    state.update(metrics=rows, items=items, unknown_tool_calls=unknown)
    for name, values in rows.items():
        print(f"  {'ok  ' if values['pass'] else 'MISS'} {name:20} {values['value']!s:>8}  target {values['target']}"
              f"  (n={values['n']})")
    problems = [f"{name} = {values['value']} (target {values['target']})" for name, values in rows.items()
                if not values["pass"]]
    warnings = [f"call to a tool that does not exist (model invented it): {u}" for u in unknown]
    return problems, warnings


def gate_human(state: dict, success: str | None, failing: str | None) -> list[str]:
    """Gate 5: both trajectories named, and both exist in Phoenix as one-root traces."""
    if not success or not failing:
        return ["name the turns you read end to end: --success-trace <id> --failing-trace <id> (rule P1)"]
    spans = metrics.fetch_spans("2000-01-01T00:00:00+00:00")
    problems = []
    for label, trace_id in (("success", success), ("failing", failing)):
        roots = [s for s in spans.get(trace_id, []) if s["name"] == "agent.turn" and not s.get("parent_id")]
        if len(roots) != 1:
            problems.append(f"{label} trace {trace_id} not found in Phoenix as one agent.turn")
    state["trajectories"] = {"success": success, "failing": failing}
    return problems


def write_report(state: dict) -> Path:
    """reports/eval.json in the EVALS §4.2 shape, plus a timestamped copy so two runs can be
    compared (SPEC §16: run twice, report both, use the lower)."""
    rows = state["metrics"]
    report = {
        "assignment": "Assignment 3: Customer Support",
        "commit": git_commit(),
        "ran_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": MODEL,
        "metrics": rows,
        "items": state["items"],
        "red_lines": metrics.red_lines(rows, secret_in_repo(), mutating_tool_loaded()),
        "trajectories": state.get("trajectories", {"success": None, "failing": None}),
        "unknown_tool_calls": state.get("unknown_tool_calls", []),
        "gates": state["gates"],
    }
    REPORTS.mkdir(exist_ok=True)
    text = json.dumps(report, indent=2, default=str)
    (REPORTS / "eval.json").write_text(text)
    stamp = re.sub(r"[:-]", "", report["ran_at"])
    (REPORTS / f"eval-{stamp}.json").write_text(text)
    return REPORTS / "eval.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Assignment 3 eval runner")
    parser.add_argument("--gates", default="0-5", help="range of gates to run, e.g. 0-3")
    parser.add_argument("--success-trace", help="trace id of the successful turn you read (gate 5)")
    parser.add_argument("--failing-trace", help="trace id of the failing turn you read (gate 5)")
    args = parser.parse_args()
    first, last = (int(x) for x in args.gates.split("-"))

    gates = Gates()
    state: dict = {"gates": {}}
    reset()
    steps = [
        (0, "STATIC", lambda: gates.report(0, "STATIC", *gate_static())),
        (1, "HEALTH", lambda: gates.report(1, "HEALTH", gate_health())),
        (2, "SMOKE", lambda: gates.report(2, "SMOKE", gate_smoke())),
        (3, "TRAJECTORY", lambda: gates.report(3, "TRAJECTORY", gate_trajectory())),
        (4, "EVAL", lambda: gates.report(4, "EVAL", *gate_eval(state))),
        (5, "HUMAN", lambda: gates.report(5, "HUMAN", gate_human(state, args.success_trace, args.failing_trace))),
    ]
    for number, name, run in steps:
        if not first <= number <= last:
            continue
        passed = run()
        state["gates"][f"{number} {name}"] = "pass" if passed else "fail"
        if not passed:
            print(f"\nStopped: gate {number} {name} failed, later gates did not run (EVALS §1, law 4).")
            break
    if "metrics" in state:  # gate 4 ran: its numbers are written even when a row missed
        print(f"\nreport: {write_report(state)}")
    code = 2 if gates.failed else (1 if gates.warned else 0)
    print(f"\nexit {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
