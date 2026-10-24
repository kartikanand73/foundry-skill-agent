"""Cortex Agent client + skill tests with an httpx MockTransport (no network, no tokens)."""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import httpx
import pytest

from agent import SkillAgent
from agent.config import Settings
from agent.fake_llm import FakeClient
from agent.snowflake import CortexAgentClient, SnowflakeConfig, SnowflakeError

ROOT = Path(__file__).resolve().parent.parent
CFG = SnowflakeConfig(account_url="https://myorg-acct.snowflakecomputing.com", database="HEALTH",
                      schema="ANALYTICS", agent="CLAIMS_AGENT")
RUN_URL = "https://myorg-acct.snowflakecomputing.com/api/v2/databases/HEALTH/schemas/ANALYTICS/agents/CLAIMS_AGENT:run"

RESPONSE = {  # shape of the final `response` event (stream=false)
    "role": "assistant",
    "content": [
        {"type": "thinking", "thinking": {"text": "Use the claims semantic view"}},
        {"type": "tool_use", "tool_use": {"tool_use_id": "t1", "name": "claims_analyst", "input": {"query": "q"}}},
        {"type": "tool_result", "tool_result": {"tool_use_id": "t1", "name": "claims_analyst", "content": [
            {"type": "json", "json": {
                "sql": "SELECT SUM(PAID_AMOUNT) AS TOTAL_PAID FROM CLAIMS WHERE QUARTER = '2026-Q1'",
                "result_set": {"resultSetMetaData": {"rowType": [{"name": "TOTAL_PAID"}]},
                               "data": [["370685724.65"]]}}}]}},
        {"type": "text", "text": "Total paid claims in Q1 2026 were $370,685,724.65."},
    ],
    "warnings": [],
    "metadata": {"run_id": "r1"},
    "status": "completed",
}


class FakeSnowflake:
    def __init__(self, status=200, body=None, first_401=False):
        self.requests: list[httpx.Request] = []
        self.status, self.body, self.first_401 = status, body or RESPONSE, first_401

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert str(req.url) == RUN_URL and req.method == "POST"
        if self.first_401:
            self.first_401 = False
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(self.status, json=self.body if self.status == 200 else
                              {"code": "399504", "message": "Agent does not exist or not authorized"})


def client(fake, pats=("PAT-1",)):
    it = iter(pats)
    return CortexAgentClient(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)),
                             pat_provider=lambda: next(it))


def test_request_contract():
    fake = FakeSnowflake()
    r = client(fake).ask("conv", "What were total paid claims in Q1 2026?")
    req = fake.requests[0]
    assert req.headers["Authorization"] == "Bearer PAT-1"
    assert req.headers["X-Snowflake-Authorization-Token-Type"] == "PROGRAMMATIC_ACCESS_TOKEN"
    assert req.headers["Accept"] == "application/json"
    body = json.loads(req.content)
    assert body == {"stream": False, "messages": [
        {"role": "user", "content": [{"type": "text", "text": "What were total paid claims in Q1 2026?"}]}]}
    assert r["answer"] == "Total paid claims in Q1 2026 were $370,685,724.65."
    assert r["sql"][0].startswith("SELECT SUM(PAID_AMOUNT)")
    assert r["tables"] == [{"columns": ["TOTAL_PAID"], "rows": [["370685724.65"]], "row_count": 1, "truncated": False}]


def test_follow_up_resends_history_per_conversation():
    fake = FakeSnowflake()
    c = client(fake)
    c.ask("conv", "q1"); c.ask("conv", "q2"); c.ask("other", "x")
    second = json.loads(fake.requests[1].content)["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"][0]["text"].startswith("Total paid claims")
    assert len(json.loads(fake.requests[2].content)["messages"]) == 1     # separate conversation
    c.end("conv")
    c.ask("conv", "fresh")
    assert len(json.loads(fake.requests[3].content)["messages"]) == 1


def test_401_rereads_token_once():
    fake = FakeSnowflake(first_401=True)
    r = client(fake, pats=("OLD", "NEW")).ask("c", "q")
    assert [q.headers["Authorization"] for q in fake.requests] == ["Bearer OLD", "Bearer NEW"]
    assert r["answer"]


def test_error_is_safe():
    with pytest.raises(SnowflakeError) as e:
        client(FakeSnowflake(status=404)).ask("c", "q")
    assert "HTTP 404" in str(e.value) and "not authorized" in str(e.value) and "PAT" not in str(e.value)


def test_large_table_truncated():
    big = json.loads(json.dumps(RESPONSE))
    big["content"][2]["tool_result"]["content"][0]["json"]["result_set"]["data"] = [[str(i)] for i in range(100)]
    r = client(FakeSnowflake(body=big)).ask("c", "q")
    assert r["tables"][0]["row_count"] == 100 and len(r["tables"][0]["rows"]) == 25 and r["tables"][0]["truncated"]


def test_config_from_env(monkeypatch):
    for k in ("SNOWFLAKE_ACCOUNT_URL", "SNOWFLAKE_AGENT_DATABASE", "SNOWFLAKE_AGENT_SCHEMA", "SNOWFLAKE_AGENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    assert SnowflakeConfig.from_env() is None
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT_URL", "myorg-acct.snowflakecomputing.com")
    monkeypatch.setenv("SNOWFLAKE_AGENT_DATABASE", "HEALTH")
    monkeypatch.setenv("SNOWFLAKE_AGENT_SCHEMA", "ANALYTICS")
    monkeypatch.setenv("SNOWFLAKE_AGENT_NAME", "CLAIMS_AGENT")
    assert CortexAgentClient(SnowflakeConfig.from_env()).run_url == RUN_URL


# ---------- through the agent ----------
@pytest.fixture
def make_agent(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    s = dataclasses.replace(Settings.from_env(), skills_dir=tmp_path / "skills", data_dir=ROOT / "data",
                            fake_model=True)
    return lambda sn: SkillAgent(s, client=FakeClient(), salesforce=False, snowflake=sn)


def test_routes_to_snowflake_and_returns_grounded_answer(make_agent):
    agent = make_agent(client(FakeSnowflake()))
    r = agent.handle("k", "What were total paid claims in Q1 2026 in Snowflake?")
    assert r.skill == "snowflake-cortex" and r.tool_calls[0]["tool"] == "ask_cortex_agent"
    assert "370,685,724.65" in r.text and r.text.startswith("From Snowflake:")
    offered = [t["function"]["name"] for t in agent.client.chat.completions.calls[1]["tools"]]
    assert offered == ["ask_cortex_agent"]


def test_snowflake_down_reported(make_agent):
    r = make_agent(client(FakeSnowflake(status=404))).handle("k", "Show claims metrics from Snowflake")
    assert r.tool_calls[0]["status"] == "error" and "Snowflake is unavailable" in r.text


def test_not_configured(make_agent):
    r = make_agent(False).handle("k", "Show revenue metrics from Snowflake")
    assert "not configured" in r.text
