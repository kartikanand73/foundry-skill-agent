"""ThreadStore against an in-memory fake TableClient."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))
import thread_store  # noqa: E402
from thread_store import ThreadStore  # noqa: E402


class FakeTable:
    def __init__(self):
        self.rows = {}

    def upsert_entity(self, e):
        self.rows[(e["PartitionKey"], e["RowKey"])] = dict(e)

    def query_entities(self, flt, parameters):
        return [dict(v) for (pk, _), v in list(self.rows.items()) if pk == parameters["pk"]]

    def delete_entity(self, partition_key, row_key):
        self.rows.pop((partition_key, row_key), None)


def thread(n=2):
    return {"title": "T", "session_id": "s1", "created": time.time(),
            "messages": [{"role": "user", "content": f"q{i}"} for i in range(n)]}


def test_save_load_isolated_by_user():
    t = FakeTable(); s = ThreadStore(t)
    s.save("alice", "t1", thread()); s.save("bob", "t2", thread())
    assert set(s.load("alice")) == {"t1"} and set(s.load("bob")) == {"t2"}
    assert s.load("alice")["t1"]["session_id"] == "s1"


def test_retention_deletes_old_threads():
    t = FakeTable(); s = ThreadStore(t, retention_days=30)
    s.save("alice", "old", thread()); t.rows[("alice", "old")]["updated"] = time.time() - 31 * 86400
    s.save("alice", "new", thread())
    assert set(s.load("alice")) == {"new"} and ("alice", "old") not in t.rows


def test_large_thread_trimmed_to_fit():
    t = FakeTable(); s = ThreadStore(t)
    big = thread(0); big["messages"] = [{"role": "user", "content": "x" * 5000} for _ in range(30)]
    s.save("alice", "t", big)
    assert len(t.rows[("alice", "t")]["messages_json"]) <= thread_store.MAX_JSON_CHARS
    assert len(s.load("alice")["t"]["messages"]) >= 1


def test_delete():
    t = FakeTable(); s = ThreadStore(t)
    s.save("alice", "t1", thread()); s.delete("alice", "t1")
    assert s.load("alice") == {}
