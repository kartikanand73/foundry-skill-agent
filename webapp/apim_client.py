"""Thin client for the skill-agent API exposed through Azure API Management.

POST {APIM_BASE_URL}/chat[?agent_session_id=<id>]
  Ocp-Apim-Subscription-Key: <key>
  {"message": "..."}
-> 200 {"response", "skill", "skill_version", "tool_calls"}
   header x-agent-session-id: <hosted session id>   (assigned by Foundry on the first call)

Each Streamlit thread keeps the session id returned on its first message and sends it on every
follow-up, so the hosted agent keeps that thread's conversation context.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx


class ApimError(Exception):
    """User-presentable error (no secrets)."""


@dataclass
class ChatResult:
    text: str
    skill: str = ""
    skill_version: str | None = None
    tool_calls: list = field(default_factory=list)
    session_id: str | None = None


class ApimClient:
    def __init__(self, base_url: str, subscription_key: str, timeout_s: float = 150.0,
                 http: httpx.Client | None = None):
        if not base_url or not subscription_key:
            raise ApimError("The app isn't configured (APIM_BASE_URL / APIM_SUBSCRIPTION_KEY).")
        self.base_url = base_url.rstrip("/")
        self.key = subscription_key
        self.http = http or httpx.Client(timeout=timeout_s)

    @staticmethod
    def from_env() -> "ApimClient":
        return ApimClient(os.environ.get("APIM_BASE_URL", ""), os.environ.get("APIM_SUBSCRIPTION_KEY", ""),
                          float(os.environ.get("APIM_TIMEOUT_SECONDS", "150")))

    def chat(self, message: str, session_id: str | None = None, user: str | None = None) -> ChatResult:
        params = {"agent_session_id": session_id} if session_id else None
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
        if user:
            headers["x-client-user"] = user[:200]
        try:
            r = self.http.post(f"{self.base_url}/chat", params=params, headers=headers,
                               json={"message": message})
        except httpx.TimeoutException:
            raise ApimError("The agent took too long to answer. Please try again.")
        except httpx.HTTPError:
            raise ApimError("Couldn't reach the agent gateway. Please try again in a moment.")
        if r.status_code == 429:
            raise ApimError("Too many requests right now - please wait a few seconds and retry.")
        if r.status_code in (401, 403):
            raise ApimError("The gateway rejected the app's credentials (check the APIM subscription key).")
        if r.status_code >= 400:
            raise ApimError(f"The agent returned an error (HTTP {r.status_code}). Please try again.")
        try:
            data = r.json()
        except ValueError:
            raise ApimError("The agent returned an unexpected response.")
        return ChatResult(
            text=data.get("response", ""),
            skill=data.get("skill", ""),
            skill_version=data.get("skill_version"),
            tool_calls=data.get("tool_calls") or [],
            session_id=r.headers.get("x-agent-session-id") or session_id,
        )
