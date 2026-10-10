"""Stage 3: a bare CLI. Log in, then send each line to the agent and print its final reply.
Stage 4 moves the turn into support/pipeline.py and turns this into a renderer."""

import asyncio
import getpass

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from support.agent import build_support_agent
from support.auth import login

APP_NAME = "customer_support"


async def main() -> None:
    email = input("Email: ").strip()
    profile = await login(email, getpass.getpass("Password: "))
    if profile is None:
        print("Invalid email or password.")
        return
    print(f"Hello, {profile['full_name']}. Type quit to leave.")

    agent, toolset = build_support_agent(profile["email"], profile["full_name"])
    sessions = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name=APP_NAME, user_id=profile["email"])

    try:
        while True:
            message = input("You: ").strip()
            if message.lower() in {"quit", "exit", "q"}:
                break
            if not message:
                continue
            content = types.Content(role="user", parts=[types.Part(text=message)])
            async for event in runner.run_async(
                user_id=profile["email"], session_id=session.id, new_message=content
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    print("Agent:", "".join(p.text or "" for p in event.content.parts))
    finally:
        await toolset.close()


if __name__ == "__main__":
    asyncio.run(main())
