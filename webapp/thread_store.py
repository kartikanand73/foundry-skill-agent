"""Per-user chat threads in Azure Table Storage.

PartitionKey = the user's Entra object id (from App Service authentication), RowKey = thread id.
The web app reaches the table with its managed identity (Storage Table Data Contributor); the storage
account has shared-key access disabled, so there is no connection string or key anywhere.

Retention: threads not updated for THREAD_RETENTION_DAYS (default 30) are deleted when the user's
threads are next loaded. Only the last MAX_MESSAGES messages of a thread are kept.
"""
from __future__ import annotations

import json
import os
import time

MAX_MESSAGES = 40
MAX_JSON_CHARS = 30_000          # Table Storage string properties are limited to 64 KiB (32K UTF-16 chars)


def _fit(messages: list[dict]) -> str:
    msgs = messages[-MAX_MESSAGES:]
    while True:
        s = json.dumps(msgs, ensure_ascii=False)
        if len(s) <= MAX_JSON_CHARS or len(msgs) <= 1:
            return s[:MAX_JSON_CHARS]
        msgs = msgs[2:]                                  # drop the oldest question + answer


class ThreadStore:
    def __init__(self, table_client, retention_days: int = 30):
        self.t = table_client
        self.retention_s = retention_days * 86400

    @staticmethod
    def from_env() -> "ThreadStore | None":
        url = os.environ.get("THREAD_STORE_URL", "").strip()
        if not url:
            return None
        from azure.data.tables import TableClient
        from azure.identity import DefaultAzureCredential
        tc = TableClient(endpoint=url, table_name=os.environ.get("THREAD_TABLE", "threads"),
                         credential=DefaultAzureCredential())
        return ThreadStore(tc, int(os.environ.get("THREAD_RETENTION_DAYS", "30")))

    def load(self, oid: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        cutoff = time.time() - self.retention_s
        for e in self.t.query_entities("PartitionKey eq @pk", parameters={"pk": oid}):
            if float(e.get("updated", 0)) < cutoff:
                self.t.delete_entity(partition_key=oid, row_key=e["RowKey"])
                continue
            out[e["RowKey"]] = {"title": e.get("title", "Chat"), "session_id": e.get("session_id") or None,
                                "created": float(e.get("created", 0)),
                                "messages": json.loads(e.get("messages_json") or "[]")}
        return out

    def save(self, oid: str, tid: str, thread: dict) -> None:
        self.t.upsert_entity({
            "PartitionKey": oid, "RowKey": tid, "title": thread["title"][:200],
            "session_id": thread.get("session_id") or "", "created": float(thread["created"]),
            "updated": time.time(), "messages_json": _fit(thread["messages"]),
        })

    def delete(self, oid: str, tid: str) -> None:
        self.t.delete_entity(partition_key=oid, row_key=tid)
