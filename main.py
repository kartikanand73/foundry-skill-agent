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
    # Foundry platform supplies a per-user id; scope demo memory by it when present.
    user_id = getattr(request.state, "user_id", None)
    key = f"{user_id}:{session_id}" if user_id else session_id

    reply = await asyncio.to_thread(agent.handle, key, message)
    return JSONResponse({"response": reply.text, "skill": reply.skill,
                         "skill_version": reply.skill_version, "tool_calls": reply.tool_calls})


if __name__ == "__main__":
    app.run()
