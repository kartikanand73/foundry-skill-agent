"""Thin client for the skill-agent API exposed through Azure API Management.

POST {APIM_BASE_URL}/chat[?agent_session_id=<id>]
  Ocp-Apim-Subscription-Key: <key>
  Authorization: Bearer <Entra token of the web app's managed identity, audience APIM_TOKEN_AUDIENCE>
  {"message": "..."}
-> 200 {"response", "skill", "skill_version", "tool_calls"}
   header x-agent-session-id: <hosted session id>   (assigned by Foundry on the first call)

Each Streamlit thread keeps the session id returned on its first message and sends it on every
follow-up, so the hosted agent keeps that thread's conversation context.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field

import httpx


def principal_from_headers(oid: str, name: str | None, encoded: str | None) -> dict:
    """{oid, name, roles} from App Service authentication headers (roles = Entra app roles)."""
    roles: list[str] = []
    if encoded:
        try:
            data = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
            role_typ = data.get("role_typ") or "roles"
            roles = sorted({c.get("val", "") for c in data.get("claims", [])
                            if c.get("typ") in (role_typ, "roles") and c.get("val")})
        except (ValueError, TypeError, AttributeError):
            roles = []
    return {"oid": oid, "name": name, "roles": roles}


class ApimError(Exception):
    """User-presentable error (no secrets)."""


@dataclass
class ChatResult:
    text: str
    skill: str = ""
    skill_version: str | None = None
    tool_calls: list = field(default_factory=list)
    session_id: str | None = None
    usage: dict | None = None       # tokens + estimated model cost for this question
    identity: dict | None = None    # what the agent says it received about the user


class ApimClient:
    def __init__(self, base_url: str, subscription_key: str, timeout_s: float = 150.0,
                 http: httpx.Client | None = None, token_provider=None):
        if not base_url or not subscription_key:
            raise ApimError("The app isn't configured (APIM_BASE_URL / APIM_SUBSCRIPTION_KEY).")
        self.base_url = base_url.rstrip("/")
        self.key = subscription_key
        self.http = http or httpx.Client(timeout=timeout_s)
        self.token_provider = token_provider   # () -> access token string; None = key only (local dev)

    @staticmethod
    def from_env() -> "ApimClient":
        provider = None
        audience = os.environ.get("APIM_TOKEN_AUDIENCE", "").strip()
        if audience:   # App Service: token from the web app's system-assigned managed identity
            from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
            provider = get_bearer_token_provider(ManagedIdentityCredential(), f"{audience}/.default")
        return ApimClient(os.environ.get("APIM_BASE_URL", ""), os.environ.get("APIM_SUBSCRIPTION_KEY", ""),
                          float(os.environ.get("APIM_TIMEOUT_SECONDS", "150")), token_provider=provider)

    def chat(self, message: str, session_id: str | None = None, user: dict | None = None,
             history: list[dict] | None = None) -> ChatResult:
        """`user` = {oid, name, roles} of the signed-in user; sent in the body to the agent.

        Trust: APIM only accepts calls carrying the web app's own managed-identity token, so this
        user context can only come from this app, which read it from App Service authentication.
        """
        params = {"agent_session_id": session_id} if session_id else None
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
        if self.token_provider:
            try:
                headers["Authorization"] = f"Bearer {self.token_provider()}"   # cached + refreshed by azure-identity
            except Exception:
                raise ApimError("The app couldn't get its sign-in token for the agent gateway. Please try again.")
        try:
            body = {"message": message}
            if user and user.get("oid"):
                body["user"] = {"oid": str(user["oid"])[:64], "name": (user.get("name") or "")[:200],
                                "roles": [str(r)[:64] for r in (user.get("roles") or [])][:20]}
            if history:   # lets the agent pick up a saved thread after its session memory has gone
                body["history"] = [{"role": h["role"], "content": str(h["content"])[:4000]}
                                   for h in history[-12:] if h.get("role") in ("user", "assistant")]
            r = self.http.post(f"{self.base_url}/chat", params=params, headers=headers, json=body)
        except httpx.TimeoutException:
            raise ApimError("The agent took too long to answer. Please try again.")
        except httpx.HTTPError:
            raise ApimError("Couldn't reach the agent gateway. Please try again in a moment.")
        if r.status_code == 429:
            raise ApimError("Too many requests right now - please wait a few seconds and retry.")
        if r.status_code in (401, 403):
            raise ApimError("The gateway rejected the app's credentials (subscription key or app identity token).")
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
            usage=data.get("usage"),
            identity=data.get("identity"),
        )
