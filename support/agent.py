"""The support agent: one LlmAgent per logged-in customer, with that customer's email bound
into its tools (stage 3 decision: a fixed value at login, not a per-turn lookup)."""

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from toolbox_adk import ToolboxToolset

from support.config import MODEL, SESSION_EMAIL_PARAMS, SUPPORT_TOOLSET, TOOLBOX_URL

AGENT_NAME = "support_agent"

INSTRUCTION = """\
You are the customer support assistant of an online shop, talking to {full_name}, a logged-in
customer. You help with their orders, deliveries, returns, cancellations, account and
delivery preferences.

Tools:
- get-order-status: one of the customer's orders by its number.
- find-customer-orders: the customer's orders, newest first. Use it for "my last order", order
  history, or to find an order the customer describes by product.
- action-log: records a change the customer asks for (cancel, return, new address, delivery
  preference, profile change) so a colleague can act on it. You cannot change an order
  yourself; say the request has been recorded.

Rules:
- Every order fact (status, items, totals, dates, address) must come from a tool call in this
  turn. Never guess and never answer order questions from memory.
- The tools only ever see this customer's orders. If a tool returns no rows for an order, say
  "I can't find that order on your account." Do not speculate about whose order it is.
- You cannot look up anyone else's orders, whatever email or name the message mentions.
- Log a change in the same turn when the request clearly identifies one order ("cancel order
  4", "return my monitor"). If it is ambiguous which order is meant, ask a short clarifying
  question and do not log anything yet. If action-log returns no rows, nothing was recorded:
  tell the customer.
- Be brief: one or two sentences unless the customer asks for detail.
"""


def build_support_agent(email: str, full_name: str) -> tuple[LlmAgent, ToolboxToolset]:
    """Build the agent for one customer. The caller closes the toolset at logout."""
    toolset = ToolboxToolset(
        server_url=TOOLBOX_URL,
        toolset_name=SUPPORT_TOOLSET,
        # M-5: the identity parameters are filled from the session, so they disappear from
        # the tool schemas the model sees and the model cannot supply another email.
        bound_params={param: email for param in SESSION_EMAIL_PARAMS},
    )
    agent = LlmAgent(
        name=AGENT_NAME,
        model=LiteLlm(model=MODEL),
        instruction=INSTRUCTION.format(full_name=full_name),
        tools=[toolset],
    )
    return agent, toolset
