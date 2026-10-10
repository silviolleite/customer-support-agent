"""The Data Masker as its own A2A service (K-1), like the Judge: an agent card and
`message/send`, through ADK's to_a2a(). Port 10003.

It is an ADK agent without a model: masking is deterministic (masking.py), so it can change
nothing but PII (K-2). Request and reply travel as JSON in the message text:

    request  {"text": "<agent reply>", "user_email": "<the customer's own email>"}
    reply    {"masked_text": "...", "counts": {"email": 0, "phone": 1, "card": 0},
              "detail": "masked 1 phone number"}

    .venv/bin/python -m guards.masker.server      # started by ./run.sh
"""

import json
import os
from collections.abc import AsyncGenerator

import uvicorn
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

from guards.masker.masking import mask

HOST = "127.0.0.1"
PORT = int(os.environ.get("MASKER_PORT", "10003"))


class MaskerAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # A malformed request raises: the task fails and the caller errors (P-4).
        request = json.loads("".join(part.text or "" for part in ctx.user_content.parts))
        result = mask(request["text"], request["user_email"])
        reply = json.dumps({"masked_text": result.text, "counts": result.counts, "detail": result.detail})
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    content=types.Content(role="model", parts=[types.Part(text=reply)]))


masker_agent = MaskerAgent(
    name="data_masker",
    description=(
        "Masks PII in a support reply: emails other than the customer's own, phone numbers and "
        'card-like numbers. Send {"text", "user_email"} as JSON; returns {"masked_text", '
        '"counts", "detail"}. Changes nothing else.'
    ),
)

app = to_a2a(masker_agent, host=HOST, port=PORT)

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
