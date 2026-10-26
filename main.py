"""Foundry Hosted Agent entry point (Invocations protocol, port 8088).

Uses azure-ai-agentserver-invocations' InvocationAgentServerHost and calls the same
SkillAgent the CLI uses. The same container also runs on Azure Container Apps.

Request:  POST /invocations?agent_session_id=<id>   {"message": "What is our vacation policy?"}
Response: {"response": "...", "skill": "hr-policy", "skill_version": "1.0.0", "tool_calls": [...]}
"""
from __future__ import annotations

import asyncio

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agent import SkillAgent
from agent.identity import parse_user
from agent.trace import instrument_http

instrument_http()   # dependency spans for model / Salesforce / Snowflake HTTP calls

agent = SkillAgent()
app = InvocationAgentServerHost()


@app.invoke_handler
async def handle_invoke(request: Request) -> Response:
    try:
        data = await request.json()
    except ValueError:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, str) or not message.strip():
        return JSONResponse({"error": "missing 'message' string"}, status_code=400)

    session_id = request.state.session_id  # resolved by the SDK (query param / platform env / generated)
    # Signed-in user from the web app (only the web app can call APIM). Conversation memory is keyed by
    # user + session, so a session id alone never reaches another user's history.
    user = parse_user(data.get("user"))
    platform_user = getattr(request.state, "user_id", None)
    owner = f"u:{user.oid}" if user else (f"p:{platform_user}" if platform_user else "anon")
    key = f"{owner}:{session_id}"

    history = data.get("history") if isinstance(data.get("history"), list) else None
    reply = await asyncio.to_thread(agent.handle, key, message, user, history)
    return JSONResponse({"response": reply.text, "skill": reply.skill,
                         "skill_version": reply.skill_version, "tool_calls": reply.tool_calls,
                         "usage": reply.usage, "identity": reply.identity})


if __name__ == "__main__":
    app.run()
