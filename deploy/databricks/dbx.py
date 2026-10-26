"""Tiny Databricks REST helper for the deploy scripts (Entra token from `az login`, nothing stored)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

DATABRICKS_RESOURCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"  # AzureDatabricks first-party app


def _az(*args: str) -> str:
    az = "az.cmd" if sys.platform == "win32" else "az"
    return subprocess.run([az, *args], capture_output=True, text=True, check=True).stdout.strip()


def workspace_url(rg: str, ws: str, sub: str) -> str:
    return "https://" + _az("resource", "show", "-g", rg, "-n", ws, "--resource-type", "Microsoft.Databricks/workspaces",
                            "--subscription", sub, "--query", "properties.workspaceUrl", "-o", "tsv")


class Dbx:
    def __init__(self, host: str):
        self.host = host.rstrip("/")
        self._tok, self._exp = None, 0.0

    def _token(self) -> str:
        if not self._tok or time.time() > self._exp:
            self._tok = _az("account", "get-access-token", "--resource", DATABRICKS_RESOURCE,
                            "--query", "accessToken", "-o", "tsv")
            self._exp = time.time() + 1800
        return self._tok

    def call(self, method: str, path: str, body=None, ok404: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self._token()}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if ok404 and e.code == 404:
                return None
            raise RuntimeError(f"{method} {path} -> {e.code}: {msg[:800]}") from None

    def sql(self, warehouse_id: str, statement: str, wait: int = 50):
        """Run one statement on the SQL warehouse and return the rows (list of lists)."""
        r = self.call("POST", "/api/2.0/sql/statements", {
            "warehouse_id": warehouse_id, "statement": statement, "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE", "format": "JSON_ARRAY", "disposition": "INLINE"})
        deadline = time.time() + 600
        while r["status"]["state"] in ("PENDING", "RUNNING") and time.time() < deadline:
            time.sleep(3)
            r = self.call("GET", f"/api/2.0/sql/statements/{r['statement_id']}")
        if r["status"]["state"] != "SUCCEEDED":
            raise RuntimeError(f"SQL failed ({r['status']['state']}): {r['status'].get('error')} :: {statement[:200]}")
        return (r.get("result") or {}).get("data_array") or []
