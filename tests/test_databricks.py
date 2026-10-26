"""Genie client + skill tests with an httpx MockTransport (no network, no tokens)."""
from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import httpx
import pytest

from agent import SkillAgent
from agent.config import Settings
from agent.databricks import GenieClient, GenieConfig, GenieError
from agent.fake_llm import FakeClient
from agent.identity import UserContext
from agent.guardrails import authorize

ROOT = Path(__file__).resolve().parent.parent
CFG = GenieConfig(host="https://adb-1.2.azuredatabricks.net", space_id="SPACE", poll_s=0)
BASE = "https://adb-1.2.azuredatabricks.net/api/2.0/genie/spaces/SPACE"
SQL = "SELECT a.account_name, SUM(o.order_total) FROM orders o JOIN accounts a ... GROUP BY 1"


class FakeGenie:
    def __init__(self, final="COMPLETED", start_status=200):
        self.requests: list[httpx.Request] = []
        self.final, self.start_status, self.polls = final, start_status, 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        u, m = str(req.url), req.method
        assert req.headers["Authorization"] == "Bearer TOKEN"
        if u == f"{BASE}/start-conversation" and m == "POST":
            if self.start_status != 200:
                return httpx.Response(self.start_status, json={"message": "PERMISSION_DENIED: no access to space"})
            return httpx.Response(200, json={"conversation_id": "C1", "message_id": "M1"})
        if u == f"{BASE}/conversations/C1/messages" and m == "POST":
            return httpx.Response(200, json={"message_id": "M2", "id": "M2"})
        if u.startswith(f"{BASE}/conversations/C1/messages/M") and "/query-result/" not in u:
            self.polls += 1
            if self.polls % 2 == 1:
                return httpx.Response(200, json={"status": "EXECUTING_QUERY"})
            if self.final != "COMPLETED":
                return httpx.Response(200, json={"status": self.final, "error": {"error": "bad question"}})
            return httpx.Response(200, json={"status": "COMPLETED", "attachments": [
                {"attachment_id": "A1", "query": {"query": SQL, "description": "Revenue by account."}},
                {"text": {"content": "Acme Corp has the highest revenue."}},
                {"suggested_questions": {"questions": ["Revenue by month?"]}}]})
        if u.endswith("/query-result/A1"):
            return httpx.Response(200, json={"statement_response": {
                "manifest": {"schema": {"columns": [{"name": "account_name"}, {"name": "revenue"}]},
                             "total_row_count": 2},
                "result": {"data_array": [["Acme Corp", "26000000.00"], ["Globex Inc", "20000000.00"]]}}})
        return httpx.Response(404, json={"message": f"unexpected {m} {u}"})


def client(fake):
    return GenieClient(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)),
                       token_provider=lambda: "TOKEN", sleep=lambda s: None)


def test_start_poll_and_result():
    fake = FakeGenie()
    r = client(fake).ask("k", "Revenue by account")
    assert r["sql"] == [SQL] and "Acme Corp has the highest revenue." in r["answer"]
    assert r["tables"][0]["columns"] == ["account_name", "revenue"] and r["tables"][0]["row_count"] == 2
    assert r["suggested_questions"] == ["Revenue by month?"]


def test_follow_up_reuses_conversation():
    fake = FakeGenie()
    c = client(fake)
    c.ask("k", "Revenue by account")
    c.ask("k", "and for Initech only?")
    assert any(str(q.url) == f"{BASE}/conversations/C1/messages" and q.method == "POST" for q in fake.requests)
    c.end("k")
    c.ask("k", "new question")
    assert sum(str(q.url).endswith("/start-conversation") for q in fake.requests) == 2


def test_failed_and_denied():
    with pytest.raises(GenieError, match="could not answer"):
        client(FakeGenie(final="FAILED")).ask("k", "q")
    with pytest.raises(GenieError, match="HTTP 403"):
        client(FakeGenie(start_status=403)).ask("k", "q")


def test_from_env(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("GENIE_SPACE_ID", raising=False)
    assert GenieConfig.from_env() is None
    monkeypatch.setenv("DATABRICKS_HOST", "adb-1.2.azuredatabricks.net")
    monkeypatch.setenv("GENIE_SPACE_ID", "SPACE")
    assert GenieClient(GenieConfig.from_env()).base == BASE


# ---------- through the agent ----------
@pytest.fixture
def make_agent(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    s = dataclasses.replace(Settings.from_env(), skills_dir=tmp_path / "skills", data_dir=ROOT / "data",
                            fake_model=True)
    return lambda g: SkillAgent(s, client=FakeClient(), salesforce=False, snowflake=False, genie=g)


def test_routes_to_genie_and_returns_grounded_answer(make_agent):
    agent = make_agent(client(FakeGenie()))
    r = agent.handle("k", "Which customer has the most orders revenue in Databricks?")
    assert r.skill == "databricks-genie" and r.tool_calls[0]["tool"] == "ask_genie"
    assert r.text.startswith("From Databricks:") and "Acme Corp" in r.text
    offered = [t["function"]["name"] for t in agent.client.chat.completions.calls[1]["tools"]]
    assert offered == ["ask_genie"]


def test_genie_down_and_not_configured(make_agent):
    r = make_agent(client(FakeGenie(start_status=403))).handle("k", "Show orders for Acme")
    assert r.tool_calls[0]["status"] == "error" and "Databricks is unavailable" in r.text
    r = make_agent(False).handle("k", "Show orders for Acme")
    assert "not configured" in r.text


def test_orders_role_required():
    u = UserContext(oid="o", name="n", roles=("SkillAgent.User", "SkillAgent.Sales"))
    assert "SkillAgent.Orders" in authorize(u, "SkillAgent.Orders", "databricks-genie", True)
    u2 = UserContext(oid="o", name="n", roles=("SkillAgent.User", "SkillAgent.Orders"))
    assert authorize(u2, "SkillAgent.Orders", "databricks-genie", True) is None
