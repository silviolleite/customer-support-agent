"""Long-term memory with Mem0, run by the pipeline as two steps, never by the model (R-1, R-6).

recall: search the customer's memories with their message, keep only the ones that are close
enough and short enough, and put them at the top of the agent's input (R-1 to R-3, R-5).
save:   store only what the customer said, never the agent's reply (R-4)."""

import os
from dataclasses import asdict, dataclass

from support import config  # noqa: F401  (loads .env and switches off Mem0's usage telemetry)

from mem0 import AsyncMemoryClient

TOP_K = 5  # T-MEM-TOPK
MIN_SCORE = 0.25  # T-MEM-MINSCORE (stage 8 decision: kept)
MAX_CHARS = 500  # T-MEM-MAXCHARS: Mem0's merge step can grow a memory without bound (R-7)
HEADER = "Relevant memories about this customer (from Mem0):"

_client: AsyncMemoryClient | None = None


def _mem0() -> AsyncMemoryClient:
    global _client
    if _client is None:
        _client = AsyncMemoryClient(api_key=os.environ["MEM0_API_KEY"])
    return _client


@dataclass
class Candidate:
    memory: str
    score: float
    inserted: bool
    reason: str | None  # why it was skipped: "below cutoff" or "too long"

    def as_event(self) -> dict:
        return asdict(self)


async def recall(message: str, user_id: str) -> list[Candidate]:
    """Every memory Mem0 returns for this message, each marked inserted or skipped (R-2, R-5)."""
    response = await _mem0().search(message, filters={"user_id": user_id}, top_k=TOP_K)
    candidates = []
    for result in response.get("results", []):
        text, score = result["memory"], float(result["score"])
        if score < MIN_SCORE:
            candidates.append(Candidate(text, score, False, "below cutoff"))
        elif len(text) > MAX_CHARS:
            candidates.append(Candidate(text[:MAX_CHARS] + "…", score, False, "too long"))
        else:
            candidates.append(Candidate(text, score, True, None))
    return candidates


def with_memories(message: str, candidates: list[Candidate]) -> str:
    """The agent's input: inserted memories at the top under a fixed header (R-3)."""
    inserted = [c.memory for c in candidates if c.inserted]
    if not inserted:
        return message
    lines = "\n".join(f"- {memory}" for memory in inserted)
    return f"{HEADER}\n{lines}\n\nCustomer message: {message}"


async def save(message: str, user_id: str) -> str:
    """Store the customer's message (only theirs, R-4). Mem0 extracts facts asynchronously,
    so the status is usually PENDING; the fact is searchable later (T-MEM-WAIT)."""
    response = await _mem0().add([{"role": "user", "content": message}], user_id=user_id)
    return str(response.get("status", "accepted"))


async def clear(user_id: str) -> None:
    """Delete every memory of one customer (the eval runner resets memory-pair users)."""
    await _mem0().delete_all(user_id=user_id)
