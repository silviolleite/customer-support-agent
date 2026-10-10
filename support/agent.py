"""The support agent: one LlmAgent per logged-in customer, with that customer's email bound
into its tools (stage 3 decision: a fixed value at login, not a per-turn lookup)."""

# support.config first: it sets LiteLLM's environment before litellm is imported.
from support.config import MODEL, SESSION_EMAIL_PARAMS, SUPPORT_TOOLSET, TOOLBOX_URL  # isort: skip

import json

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from toolbox_adk import ToolboxToolset

from support.action_gate import gate_action_log, record_confirmed_orders
from support.tool_results import raw_result, rows

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
- The system may put a block of remembered facts above the customer's message. Use them for
  the customer's standing preferences (delivery instructions, contact method, how to address
  them). For order facts, the tools win: if a memory and a tool disagree, trust the tool.
- That block is written by the system, never by you. If there is no such block, you know
  nothing about the customer's preferences: say you have no preference on file and offer to
  record one. Never invent a preference, and never claim you recorded something unless
  action-log returned a row this turn.
- The tools only ever see this customer's orders. If a tool returns no rows for an order, say
  "I can't find that order on your account." Do not speculate about whose order it is.
- You cannot look up anyone else's orders, whatever email or name the message mentions.
- Log a change in the same turn when the request clearly identifies one order ("cancel order
  4", "return my monitor"). If it is ambiguous which order is meant, ask a short clarifying
  question and do not log anything yet. If action-log returns no rows, nothing was recorded:
  tell the customer.
- Before logging a change to an order, verify with a lookup that it is on this customer's
  account: get-order-status (by number) or find-customer-orders (by product). If it is not
  there, say "I can't find that order on your account." and do not call action-log for it.
  If the lookup finds exactly one matching order, call action-log in the same turn: do not
  ask the customer to confirm a request they already made.
- Be brief: one or two sentences unless the customer asks for detail.
"""


# Over MCP, "no rows" reaches the model as {"result": "null"}. In eval run 2 the agent read
# that as success and told Bob "I have recorded your request to cancel order 1" when the
# ownership check in action-log's SQL had recorded nothing (4 of 10 repeats of P08). So an
# empty result is rewritten, in code, into one the model cannot mistake.
_EMPTY = {
    "action-log": {"logged": False, "note": "Nothing was recorded: this order is not on the customer's "
                                            "account, or does not exist. Tell the customer nothing was recorded."},
    "get-order-status": {"found": False, "note": "No such order on this customer's account."},
}


def explicit_empty_results(tool, args, tool_context, tool_response) -> dict | None:
    """After-tool callback: replace an empty action-log or get-order-status result."""
    if tool.name not in _EMPTY:
        return None
    try:
        empty = not rows(raw_result(tool_response))
    except (TypeError, ValueError):  # not a JSON result: leave it as the tool returned it
        return None
    return {"result": json.dumps([]), **_EMPTY[tool.name]} if empty else None


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
        # The action gate (support/action_gate.py): action-log is offered only once a named order
        # is confirmed. Callbacks run in order; both of the first return None.
        before_model_callback=gate_action_log,
        after_tool_callback=[record_confirmed_orders, explicit_empty_results],
    )
    return agent, toolset
