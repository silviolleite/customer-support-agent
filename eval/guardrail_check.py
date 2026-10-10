"""Measure the Guardrail alone against the legitimate and off-topic sets, plus the memory
plants (M05: "allergic to peanuts" must pass), for the stage 7 check.

A development tool for the prompt-version table in BUILD_LOG.md: it calls guardrail.check()
directly, without the Sanitizer or the Judge, so it isolates the prompt. The graded numbers
come from the full pipeline in stage 10 (eval/run.py), where an earlier guard can also block.

    .venv/bin/python -m eval.guardrail_check            # all three
    .venv/bin/python -m eval.guardrail_check legit      # one set
"""

import asyncio
import json
import sys
from pathlib import Path

from guards import guardrail

SETS = {"legit": "safe", "offtopic": "unsafe", "plants": "safe"}  # set -> the decision it should get
CONCURRENCY = 5


def load(name: str) -> list[dict]:
    if name == "plants":  # the plant turn of each memory pair is a customer message too
        rows = [json.loads(line) for line in Path("eval/gold/memory.jsonl").read_text().splitlines()]
        return [{"id": r["id"], "email": r["email"], "message": r["plant"]} for r in rows]
    return [json.loads(line) for line in Path(f"eval/gold/{name}.jsonl").read_text().splitlines()]


async def _classify(item: dict, limit: asyncio.Semaphore) -> tuple[dict, str, str]:
    async with limit:
        try:
            decision, _ = await guardrail.check(item["message"], item["email"])
            return item, decision.decision, decision.reasoning
        except Exception as exc:  # reported as an error, never counted as either decision
            return item, "error", f"{exc.__class__.__name__}: {exc}"


async def main(names: list[str]) -> None:
    limit = asyncio.Semaphore(CONCURRENCY)
    for name in names:
        items = load(name)
        results = await asyncio.gather(*(_classify(item, limit) for item in items))
        expected = SETS[name]
        print(f"\n== {name}: expected {expected!r}")
        for item, decision, reasoning in results:
            mark = "  " if decision == expected else "✘ "
            print(f"{mark}{item['id']} {decision:6} {item['message']}\n      {reasoning}")
        wrong = sum(d != expected for _, d, _ in results)
        errors = sum(d == "error" for _, d, _ in results)
        if expected == "safe":
            print(f"-- {name} false blocks: {wrong - errors}/{len(items)}, errors: {errors}")
        else:
            print(f"-- off-topic blocked: {len(items) - wrong}/{len(items)}, errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(SETS)))
