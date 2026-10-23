"""Client for the Salesforce Agentforce Agent API.

Flow (per https://developer.salesforce.com/docs/einstein/genai/guide/agent-api-get-started.html):
  1. POST {my_domain}/services/oauth2/token        grant_type=client_credentials  -> access_token
  2. POST {api}/einstein/ai-agent/v1/agents/{agent_id}/sessions                     -> sessionId
  3. POST {api}/einstein/ai-agent/v1/sessions/{sessionId}/messages  {message: {sequenceId, type: Text, text}}
  4. DELETE {api}/einstein/ai-agent/v1/sessions/{sessionId}   (x-session-end-reason: UserRequest)

One Agentforce session is kept per conversation (our session key), so follow-ups keep their
context on the Salesforce side. Idle sessions expire and are recreated automatically.

The client secret is never logged. It comes from Key Vault (hosted: read with the agent's managed
identity) or, for local development only, from the SALESFORCE_CLIENT_SECRET environment variable.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass

import httpx

from .trace import trace


class SalesforceError(Exception):
    """Safe-to-show failure (no tokens, no secrets)."""


@dataclass(frozen=True)
class SalesforceConfig:
    my_domain: str          # https://<mydomain>.my.salesforce.com  (NOT lightning.force.com)
    client_id: str          # External Client App consumer key
    agent_id: str           # Agentforce agent (BotDefinition) id, 0Xx...
    api_base: str = "https://api.salesforce.com"   # api.gov.salesforce.com for Government Cloud
    keyvault_url: str = ""  # https://<vault>.vault.azure.net/  (preferred for the secret)
    secret_name: str = "salesforce-client-secret"
    timeout_s: float = 60.0
    session_idle_s: float = 600.0

    @staticmethod
    def from_env() -> "SalesforceConfig | None":
        e = os.environ.get
        domain, cid, agent = (e("SALESFORCE_MY_DOMAIN", "").strip(), e("SALESFORCE_CLIENT_ID", "").strip(),
                              e("SALESFORCE_AGENTFORCE_ID", "").strip())
        if not (domain and cid and agent):
            return None
        if not domain.startswith("https://"):
            domain = "https://" + domain
        return SalesforceConfig(
            my_domain=domain.rstrip("/"), client_id=cid, agent_id=agent,
            api_base=e("SALESFORCE_API_BASE", "https://api.salesforce.com").rstrip("/"),
            keyvault_url=e("SALESFORCE_KEYVAULT_URL", "").strip(),
            secret_name=e("SALESFORCE_SECRET_NAME", "salesforce-client-secret"),
            timeout_s=float(e("SALESFORCE_TIMEOUT_SECONDS", "60")),
            session_idle_s=float(e("SALESFORCE_SESSION_IDLE_SECONDS", "600")),
        )


def _secret_from_keyvault(cfg: SalesforceConfig) -> str:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    return SecretClient(cfg.keyvault_url, DefaultAzureCredential()).get_secret(cfg.secret_name).value


@dataclass
class _Session:
    id: str
    seq: int
    last_used: float


class AgentforceClient:
    def __init__(self, cfg: SalesforceConfig, http: httpx.Client | None = None, secret_provider=None):
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=cfg.timeout_s)
        self._secret_provider = secret_provider or self._default_secret
        self._token: str | None = None
        self._token_at = 0.0
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    # ---------- auth ----------
    def _default_secret(self) -> str:
        if self.cfg.keyvault_url:
            return _secret_from_keyvault(self.cfg)
        s = os.environ.get("SALESFORCE_CLIENT_SECRET", "")
        if not s:
            raise SalesforceError("Salesforce client secret not configured")
        return s

    def _access_token(self, force: bool = False) -> str:
        # Client-credentials tokens don't return expires_in; refresh every 25 minutes or on 401.
        if self._token and not force and time.time() - self._token_at < 1500:
            return self._token
        r = self.http.post(f"{self.cfg.my_domain}/services/oauth2/token", data={
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self._secret_provider(),
        })
        if r.status_code != 200:
            trace("sf_auth_error", status=r.status_code)
            raise SalesforceError(f"Salesforce authentication failed (HTTP {r.status_code})")
        self._token, self._token_at = r.json()["access_token"], time.time()
        trace("sf_auth", status="ok")
        return self._token

    def _send(self, method: str, url: str, **kw) -> httpx.Response:
        """Authenticated request; retries once with a fresh token on 401."""
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._access_token(force=attempt == 1)}", **kw.pop("headers", {})}
            r = self.http.request(method, url, headers=headers, **kw)
            if r.status_code != 401:
                return r
        return r

    # ---------- sessions ----------
    def _start_session(self) -> _Session:
        r = self._send("POST", f"{self.cfg.api_base}/einstein/ai-agent/v1/agents/{self.cfg.agent_id}/sessions",
                       json={
                           "externalSessionKey": str(uuid.uuid4()),
                           "instanceConfig": {"endpoint": self.cfg.my_domain},
                           "streamingCapabilities": {"chunkTypes": ["Text"]},
                           "bypassUser": True,
                       })
        if r.status_code not in (200, 201):
            trace("sf_session_error", status=r.status_code)
            raise SalesforceError(f"Could not start Agentforce session (HTTP {r.status_code})")
        sid = r.json()["sessionId"]
        trace("sf_session", action="started", session=sid[:8] + "...")
        return _Session(sid, 0, time.time())

    def _end_session(self, s: _Session) -> None:
        try:
            self._send("DELETE", f"{self.cfg.api_base}/einstein/ai-agent/v1/sessions/{s.id}",
                       headers={"x-session-end-reason": "UserRequest"})
            trace("sf_session", action="ended", session=s.id[:8] + "...")
        except httpx.HTTPError:
            pass  # best effort; Salesforce also times sessions out

    def end(self, key: str) -> None:
        with self._lock:
            s = self._sessions.pop(key, None)
        if s:
            self._end_session(s)

    def _session_for(self, key: str) -> _Session:
        with self._lock:
            s = self._sessions.get(key)
        if s and time.time() - s.last_used > self.cfg.session_idle_s:
            self._end_session(s)
            s = None
        if not s:
            s = self._start_session()
            with self._lock:
                self._sessions[key] = s
        return s

    # ---------- the call the tool makes ----------
    def ask(self, key: str, text: str) -> dict:
        """Send one message to the Agentforce agent within this conversation's session."""
        for attempt in (0, 1):
            s = self._session_for(key)
            s.seq += 1
            r = self._send("POST", f"{self.cfg.api_base}/einstein/ai-agent/v1/sessions/{s.id}/messages",
                           headers={"Accept": "application/json"},
                           json={"message": {"sequenceId": s.seq, "type": "Text", "text": text}})
            if r.status_code in (404, 410) and attempt == 0:   # session expired on Salesforce's side
                with self._lock:
                    self._sessions.pop(key, None)
                trace("sf_session", action="expired_recreating")
                continue
            break
        if r.status_code != 200:
            trace("sf_message_error", status=r.status_code)
            raise SalesforceError(f"Agentforce returned HTTP {r.status_code}")
        s.last_used = time.time()
        msgs = r.json().get("messages", [])
        replies = [m.get("message", "") for m in msgs if m.get("type") == "Inform" and m.get("message")]
        unsafe = any(m.get("isContentSafe") is False for m in msgs)
        return {
            "answer": "\n\n".join(replies) if replies else "",
            "message_types": sorted({m.get("type", "?") for m in msgs}),
            "content_safe": not unsafe,
            "source": f"Salesforce Agentforce agent {self.cfg.agent_id}",
        }
