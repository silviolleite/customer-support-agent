"""Repeat cross-user probes to measure a leak *rate*, not one sample (stage 10 check).

A single eval run is one draw from a model: P08 passed in run 1 and leaked in run 2. This runs
chosen probe items N times through /api/chat and counts, per item, the turns that leaked by
the EVALS §3.4 rules, the action-log calls, and replies that claim something was recorded.

    .venv/bin/python -m eval.probe_repeat                  # P05 P08 P10, 10 times each
    .venv/bin/python -m eval.probe_repeat P08 --times 20
"""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from eval import checks, metrics
from eval.client import web_turn

DEFAULT_ITEMS = ["P05", "P08", "P10"]  # the probes that ask to act on or read another's order


async def main(item_ids: list[str], times: int) -> None:
    items = {i["id"]: i for i in map(json.loads, Path("eval/gold/probe.jsonl").read_text().splitlines())}
    owners = checks.order_owners()
    async with httpx.AsyncClient(timeout=30) as client:
        for item_id in item_ids:
            item = items[item_id]
            leaked = action_calls = claimed = errors = 0
            for _ in range(times):
                events = await web_turn(client, item["email"], item["user"], item["message"])
                turn = {"item": item, "events": events}
                leaked += bool(metrics.leaks(turn, owners))
                action_calls += sum(c["name"] == "action-log" for c in checks.tool_calls(events))
                end = checks.terminal(events)
                errors += end.get("type") != "final"  # an error turn proves nothing either way
                claimed += "recorded" in end.get("response", "").lower()
            print(f"{item_id} ({item['user']}: {item['message']!r}) x{times}: "
                  f"leaked {leaked}, action-log calls {action_calls}, replies saying 'recorded' {claimed}, "
                  f"error turns {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("items", nargs="*", default=DEFAULT_ITEMS)
    parser.add_argument("--times", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(main(args.items, args.times))
