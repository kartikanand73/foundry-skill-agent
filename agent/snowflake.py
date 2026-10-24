"""Client for an existing Snowflake Cortex Agent object (Cortex Agents Run API).

  POST https://<account>.snowflakecomputing.com/api/v2/databases/{db}/schemas/{schema}/agents/{name}:run
  Authorization: Bearer <programmatic access token>
  X-Snowflake-Authorization-Token-Type: PROGRAMMATIC_ACCESS_TOKEN
  {"messages": [...], "stream": false}            -> single JSON = payload of the final `response` event

Docs: https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-agents-run

Conversation context: we resend the last few user/assistant turns for this conversation in
`messages` (chronological), which the Run API supports without server-side threads.

The PAT is never logged. Hosted: read from Key Vault with the agent's managed identity.
Local dev only: SNOWFLAKE_PAT environment variable.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass

import httpx

from .trace import trace


class SnowflakeError(Exception):
    """Safe-to-show failure (no tokens)."""


@dataclass(frozen=True)
class SnowflakeConfig:
    account_url: str      # https://<orgname>-<account>.snowflakecomputing.com
    database: str
    schema: str
    agent: str
    keyvault_url: str = ""
    secret_name: str = "snowflake-pat"
    timeout_s: float = 120.0
    history_turns: int = 4

    @staticmethod
    def from_env() -> "SnowflakeConfig | None":
        e = lambda k, d="": os.environ.get(k, d).strip()
        url, db, sch, ag = e("SNOWFLAKE_ACCOUNT_URL"), e("SNOWFLAKE_AGENT_DATABASE"), \
            e("SNOWFLAKE_AGENT_SCHEMA"), e("SNOWFLAKE_AGENT_NAME")
        if not (url and db and sch and ag):
            return None
        if not url.startswith("https://"):
            url = "https://" + url
        return SnowflakeConfig(
            account_url=url.rstrip("/"), database=db, schema=sch, agent=ag,
            keyvault_url=e("SNOWFLAKE_KEYVAULT_URL") or e("SALESFORCE_KEYVAULT_URL"),
            secret_name=e("SNOWFLAKE_SECRET_NAME", "snowflake-pat"),
            timeout_s=float(e("SNOWFLAKE_TIMEOUT_SECONDS", "120")),
            history_turns=int(e("SNOWFLAKE_HISTORY_TURNS", "4")),
        )


def _find(obj, key: str, out: list, limit: int = 5):
    """Collect values of `key` anywhere in a nested JSON structure."""
    if len(out) >= limit:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and v not in (None, "", [], {}):
                out.append(v)
            else:
                _find(v, key, out, limit)
    elif isinstance(obj, list):
        for v in obj:
            _find(v, key, out, limit)


def _compact_result_set(rs: dict, max_rows: int = 25) -> dict:
    cols = [c.get("name") for c in (rs.get("resultSetMetaData") or {}).get("rowType", [])]
    rows = rs.get("data") or []
    return {"columns": cols, "rows": rows[:max_rows], "row_count": len(rows),
            "truncated": len(rows) > max_rows}


class CortexAgentClient:
    def __init__(self, cfg: SnowflakeConfig, http: httpx.Client | None = None, pat_provider=None):
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=cfg.timeout_s)
        self._pat_provider = pat_provider or self._default_pat
        self._pat: str | None = None
        self._history: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _default_pat(self) -> str:
        if self.cfg.keyvault_url:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            return SecretClient(self.cfg.keyvault_url, DefaultAzureCredential()).get_secret(self.cfg.secret_name).value
        pat = os.environ.get("SNOWFLAKE_PAT", "")
        if not pat:
            raise SnowflakeError("Snowflake access token not configured")
        return pat

    def _token(self, refresh: bool = False) -> str:
        if self._pat is None or refresh:          # re-read (e.g. after rotation) on 401
            self._pat = self._pat_provider()
        return self._pat

    @property
    def run_url(self) -> str:
        c = self.cfg
        return f"{c.account_url}/api/v2/databases/{c.database}/schemas/{c.schema}/agents/{c.agent}:run"

    def end(self, key: str) -> None:
        with self._lock:
            self._history.pop(key, None)

    def ask(self, key: str, question: str) -> dict:
        with self._lock:
            hist = list(self._history.get(key, ()))
        messages = hist + [{"role": "user", "content": [{"type": "text", "text": question}]}]
        body = {"messages": messages, "stream": False}
        for attempt in (0, 1):
            r = self.http.post(self.run_url, json=body, headers={
                "Authorization": f"Bearer {self._token(refresh=attempt == 1)}",
                "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
                "Content-Type": "application/json", "Accept": "application/json"})
            if r.status_code != 401:
                break
        if r.status_code != 200:
            detail = ""
            try:
                j = r.json()
                detail = f": {j.get('message', '')[:200]}" if isinstance(j, dict) else ""
            except ValueError:
                pass
            trace("sn_error", status=r.status_code)
            raise SnowflakeError(f"Cortex Agent returned HTTP {r.status_code}{detail}")
        data = r.json()
        content = data.get("content", []) if isinstance(data, dict) else []
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text" and c.get("text"))
        sql: list = []
        _find(content, "sql", sql, limit=3)
        rsets: list = []
        _find(content, "result_set", rsets, limit=3)
        trace("sn_run", status=data.get("status", "ok"), sql=len(sql), tables=len(rsets))
        with self._lock:
            h = self._history.setdefault(key, deque(maxlen=2 * self.cfg.history_turns))
            h.append({"role": "user", "content": [{"type": "text", "text": question}]})
            if text:
                h.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
        return {
            "answer": text,
            "sql": [s for s in sql if isinstance(s, str)],
            "tables": [_compact_result_set(rs) for rs in rsets if isinstance(rs, dict)],
            "warnings": [w.get("message") for w in data.get("warnings", []) if isinstance(w, dict)],
            "status": data.get("status", "completed"),
            "source": f"Snowflake Cortex Agent {self.cfg.database}.{self.cfg.schema}.{self.cfg.agent}",
        }
