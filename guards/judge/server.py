"""The Security Judge as its own A2A service (J-1): an agent card at /.well-known/ and a
JSON-RPC endpoint taking `message/send`, built by ADK's to_a2a() around the judge agent.

    .venv/bin/python -m guards.judge.server      # started by ./run.sh, port 10002
"""

import os

import uvicorn
from google.adk.a2a.utils.agent_to_a2a import to_a2a

from guards.judge.agent import judge_agent

HOST = "127.0.0.1"
PORT = int(os.environ.get("JUDGE_PORT", "10002"))

app = to_a2a(judge_agent, host=HOST, port=PORT)

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
