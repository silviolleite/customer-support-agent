"""Settings shared by every front end. Values come from .env (never from the repo)."""

import os

from dotenv import load_dotenv

load_dotenv()

# LiteLLM fetches its model price table from GitHub on first use, retrying with backoff when
# the network is slow or down. It cost a stage-4 turn ~20 s of its 24.7 s; the copy bundled
# with the package is enough. Must be set before litellm is first used.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

MODEL = os.environ.get("MODEL", "openrouter/google/gemini-2.5-flash")
TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "http://127.0.0.1:5000")

# The agent's toolset (tools.yaml). It holds no tool that can change customer_orders (M-6).
SUPPORT_TOOLSET = "support_agent"
# Tool parameters that carry the customer's identity. They are bound from the session
# and never shown to the model (M-5).
SESSION_EMAIL_PARAMS = ("customer_email", "user_email")
