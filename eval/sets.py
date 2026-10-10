"""Run the seven gold sets through POST /api/chat, in the order the stage 10 decision chose.

Memory pairs are kept apart from every turn of their own users: the other sets save memories
too, and in eval run 1 (when legit, probe, order and action ran during the wait) two planted
memories never came back. So only alice's sets, attack and off-topic, fill the wait:

    round 1: clear + plant the first pair of each memory user
             attack, off-topic (all alice)              the rest of T-MEM-WAIT is spent idle
             ask round 1
    round 2: clear + plant the second pair (diana, george, hannah: "pairs for one user in order")
             wait T-MEM-WAIT, ask round 2
    then:    legit; reset the database (EVALS §3.2: legit wrote actions_log); probe, order;
             snapshot customer_orders, action set, snapshot again   (T-MUTATE, byte for byte)
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path

import httpx

from eval import checks
from eval.client import web_turn
from support import memory

GOLD = Path("eval/gold")
# T-MEM-WAIT is >= 120 s, a minimum (SPEC §16: "wait longer; never shorten it to pass"). Eval
# run 4 asked M01, M03 and M04 at 120 s and Mem0 had not extracted them yet (0 results, no
# error), while runs 2 and 3 found them at the same wait. 180 s leaves room for a slow queue.
MEM_WAIT_S = 180


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (GOLD / f"{name}.jsonl").read_text().splitlines()]


def orders_snapshot() -> str:
    """The whole customer_orders table, read as the admin by the harness (not the app)."""
    result = subprocess.run(["./run.sh", "psql", "-tA", "-c", "SELECT * FROM customer_orders ORDER BY order_id"],
                            capture_output=True, text=True, check=True)
    return result.stdout


class Runner:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.turns: list[dict] = []
        self.planted_at: dict[str, float] = {}
        self.waits: dict[str, float] = {}

    async def turn(self, set_name: str, item: dict, message: str, role: str | None = None) -> None:
        started = time.monotonic()
        try:
            events = await web_turn(self.client, item["email"], item["user"], message)
        except (httpx.HTTPError, ValueError) as exc:  # counted as an error turn, never skipped
            events = [{"type": "harness_error", "error": f"{exc.__class__.__name__}: {exc}"}]
        end = checks.terminal(events)
        outcome = end.get("terminated") or events[-1].get("type")
        where = end.get("blocked_at") or end.get("step") or ""
        label = f"{item['id']}{'/' + role if role else ''}"
        print(f"  {set_name:8} {label:10} {outcome:8} {where:10} {int((time.monotonic() - started) * 1000):6} ms",
              flush=True)
        self.turns.append({"set": set_name, "item": item, "role": role, "events": events})

    async def plant(self, pairs: list[dict]) -> None:
        for email in {p["email"] for p in pairs}:
            await memory.clear(email)  # EVALS §3.7: clear that user's memories first
        await asyncio.sleep(5)  # Mem0 deletes asynchronously
        for pair in pairs:
            await self.turn("memory", pair, pair["plant"], "plant")
            self.planted_at[pair["id"]] = time.monotonic()

    async def ask(self, pairs: list[dict]) -> None:
        for pair in pairs:
            remaining = self.planted_at[pair["id"]] + MEM_WAIT_S - time.monotonic()
            if remaining > 0:
                print(f"  (waiting {remaining:.0f} s for Mem0: T-MEM-WAIT)", flush=True)
                await asyncio.sleep(remaining)
            self.waits[pair["id"]] = time.monotonic() - self.planted_at[pair["id"]]
            await self.turn("memory", pair, pair["ask"], "ask")

    async def run_set(self, name: str) -> None:
        for item in load(name):
            await self.turn(name, item, item["message"])


async def run_all() -> dict:
    pairs = load("memory")
    first_pair, round1, round2 = set(), [], []
    for pair in pairs:
        (round2 if pair["user"] in first_pair else round1).append(pair)
        first_pair.add(pair["user"])

    async with httpx.AsyncClient(timeout=30) as client:
        runner = Runner(client)
        print("round 1: plant", flush=True)
        await runner.plant(round1)
        for name in ("attack", "offtopic"):  # every item runs as alice, who has no memory pair
            print(f"set {name}", flush=True)
            await runner.run_set(name)
        print("round 1: ask", flush=True)
        await runner.ask(round1)

        print("round 2: plant", flush=True)
        await runner.plant(round2)
        print("round 2: ask", flush=True)
        await runner.ask(round2)

        print("set legit", flush=True)
        await runner.run_set("legit")
        subprocess.run(["./run.sh", "reset"], check=True, capture_output=True)
        for name in ("probe", "order"):
            print(f"set {name}", flush=True)
            await runner.run_set(name)
        before = orders_snapshot()
        print("set action", flush=True)
        await runner.run_set("action")
        after = orders_snapshot()

    return {"turns": runner.turns, "orders_unchanged": before == after, "waits": runner.waits}
