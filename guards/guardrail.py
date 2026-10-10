"""The Guardrail: an in-process ADK agent that decides whether a message is in scope for this
shop's support desk (GR-1, GR-2). Its prompt lives in guardrail_prompt.md, written by hand.

Each check runs in a fresh ADK session that is deleted afterwards, so one message's verdict
cannot colour the next (GR-3). Anything but a valid {decision, reasoning} raises, and the
pipeline turns that into an error, never a pass (GR-4)."""

from pathlib import Path
from typing import Literal

from support.config import MODEL  # isort: skip  (sets LiteLLM's environment first)

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

PROMPT = (Path(__file__).parent / "guardrail_prompt.md").read_text()
APP_NAME = "guardrail"


class Decision(BaseModel):
    decision: Literal["safe", "unsafe"]
    reasoning: str


guardrail_agent = LlmAgent(
    name="guardrail_agent",
    model=LiteLlm(model=MODEL),
    # A callable, not a string: ADK fills {placeholders} in string instructions from session
    # state, and the prompt's JSON examples are full of braces.
    instruction=lambda _context: PROMPT,
    output_schema=Decision,
)

_sessions = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=guardrail_agent, session_service=_sessions)


async def check(message: str, user_id: str) -> tuple[Decision, dict]:
    """Classify one message. Returns the decision and the tokens it cost {"in", "out"}."""
    session = await _sessions.create_session(app_name=APP_NAME, user_id=user_id)
    text, tokens = "", {"in": 0, "out": 0}
    try:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        async for event in _runner.run_async(user_id=user_id, session_id=session.id, new_message=content):
            if event.usage_metadata:
                tokens["in"] += event.usage_metadata.prompt_token_count or 0
                tokens["out"] += event.usage_metadata.candidates_token_count or 0
            if event.is_final_response() and event.content and event.content.parts:
                text = "".join(p.text or "" for p in event.content.parts if not p.thought)
    finally:
        await _sessions.delete_session(app_name=APP_NAME, user_id=user_id, session_id=session.id)
    return Decision.model_validate_json(text), tokens  # raises ValueError if not exactly a Decision
