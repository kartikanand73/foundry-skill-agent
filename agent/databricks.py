"""Client for a Databricks AI/BI Genie space (Genie Conversation API).

  POST {host}/api/2.0/genie/spaces/{space}/start-conversation           {"content": q}
  POST {host}/api/2.0/genie/spaces/{space}/conversations/{cid}/messages {"content": q}   (follow-up)
  GET  .../conversations/{cid}/messages/{mid}                            poll until COMPLETED/FAILED/CANCELLED
  GET  .../conversations/{cid}/messages/{mid}/query-result/{attachment}  rows of the SQL Genie ran

Auth: an Entra token for the AzureDatabricks resource, from the agent's own identity
(DefaultAzureCredential) - no secret anywhere. The identity is a Databricks service principal with
CAN_USE on the warehouse, CAN RUN on the space and SELECT on the orders schema only.
One Genie conversation per agent session, so follow-ups keep their context.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import httpx

from .trace import trace

DATABRICKS_SCOPE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"
_DONE = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}


class GenieError(Exception):
    """Safe-to-show failure (no tokens)."""


@dataclass(frozen=True)
class GenieConfig:
    host: str            # https://adb-<id>.<n>.azuredatabricks.net
    space_id: str
    timeout_s: float = 120.0
    poll_s: float = 2.0
    max_rows: int = 25

    @staticmethod
    def from_env() -> "GenieConfig | None":
        e = lambda k, d="": os.environ.get(k, d).strip()
        host, space = e("DATABRICKS_HOST"), e("GENIE_SPACE_ID")
        if not (host and space):
            return None
        if not host.startswith("https://"):
            host = "https://" + host
        return GenieConfig(host=host.rstrip("/"), space_id=space,
                           timeout_s=float(e("GENIE_TIMEOUT_SECONDS", "120")))


def _default_token_provider():
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    return get_bearer_token_provider(DefaultAzureCredential(), DATABRICKS_SCOPE)


class GenieClient:
    def __init__(self, cfg: GenieConfig, http: httpx.Client | None = None, token_provider=None, sleep=time.sleep):
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=60)
        self._tp = token_provider
        self._sleep = sleep
        self._conv: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def base(self) -> str:
        return f"{self.cfg.host}/api/2.0/genie/spaces/{self.cfg.space_id}"

    def _headers(self) -> dict:
        if self._tp is None:
            self._tp = _default_token_provider()
        return {"Authorization": f"Bearer {self._tp()}", "Content-Type": "application/json"}

    def _req(self, method: str, url: str, **kw) -> dict:
        r = self.http.request(method, url, headers=self._headers(), **kw)
        if r.status_code != 200:
            detail = ""
            try:
                j = r.json()
                detail = f": {str(j.get('message', ''))[:200]}" if isinstance(j, dict) else ""
            except ValueError:
                pass
            trace("genie_error", status=r.status_code)
            raise GenieError(f"Genie returned HTTP {r.status_code}{detail}")
        return r.json()

    def end(self, key: str) -> None:
        with self._lock:
            self._conv.pop(key, None)

    def ask(self, key: str, question: str) -> dict:
        with self._lock:
            cid = self._conv.get(key)
        if cid:
            m = self._req("POST", f"{self.base}/conversations/{cid}/messages", json={"content": question})
            mid = m.get("message_id") or m.get("id")
        else:
            s = self._req("POST", f"{self.base}/start-conversation", json={"content": question})
            cid = s.get("conversation_id") or (s.get("conversation") or {}).get("id")
            mid = s.get("message_id") or (s.get("message") or {}).get("id")
            with self._lock:
                self._conv[key] = cid
        url = f"{self.base}/conversations/{cid}/messages/{mid}"
        deadline = time.monotonic() + self.cfg.timeout_s
        msg = self._req("GET", url)
        while msg.get("status") not in _DONE:
            if time.monotonic() > deadline:
                raise GenieError("Genie did not finish in time")
            self._sleep(self.cfg.poll_s)
            msg = self._req("GET", url)
        status = msg.get("status")
        if status != "COMPLETED":
            err = (msg.get("error") or {}).get("error") or status
            trace("genie_run", status=status)
            raise GenieError(f"Genie could not answer ({str(err)[:200]})")
        texts, sqls, descs, tables, suggested = [], [], [], [], []
        for att in msg.get("attachments") or []:
            if att.get("text"):
                texts.append(att["text"].get("content", ""))
            q = att.get("query")
            if q:
                sqls.append(q.get("query", ""))
                if q.get("description"):
                    descs.append(q["description"])
                aid = att.get("attachment_id") or att.get("id")
                if aid:
                    tables.append(self._result(url, aid))
            if att.get("suggested_questions"):
                suggested.extend((att["suggested_questions"] or {}).get("questions", []))
        trace("genie_run", status=status, sql=len(sqls), tables=len(tables))
        return {
            "answer": "\n".join(t for t in texts + descs if t),
            "sql": [s for s in sqls if s],
            "tables": tables,
            "suggested_questions": suggested[:3],
            "status": "completed",
            "source": "Databricks Genie (orders)",
        }

    def _result(self, msg_url: str, attachment_id: str) -> dict:
        r = self._req("GET", f"{msg_url}/query-result/{attachment_id}")
        sr = r.get("statement_response") or r
        cols = [c.get("name") for c in (((sr.get("manifest") or {}).get("schema") or {}).get("columns") or [])]
        rows = (sr.get("result") or {}).get("data_array") or []
        n = (sr.get("manifest") or {}).get("total_row_count", len(rows))
        return {"columns": cols, "rows": rows[: self.cfg.max_rows], "row_count": n,
                "truncated": n > self.cfg.max_rows}
