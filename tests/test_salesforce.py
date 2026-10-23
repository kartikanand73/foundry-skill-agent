"""Agentforce client + skill tests. Salesforce is replaced by an httpx MockTransport that
checks every request the client sends (URL, method, headers, body) - no network, no secrets."""
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
from agent.salesforce import AgentforceClient, SalesforceConfig, SalesforceError

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "https://contoso.my.salesforce.com"
API = "https://api.salesforce.com/einstein/ai-agent/v1"
CFG = SalesforceConfig(my_domain=DOMAIN, client_id="cid", agent_id="0XxAGENT")


class FakeSalesforce:
    """Minimal Agentforce Agent API stand-in that records requests."""

    def __init__(self, answer="Acme Corp has 2 open opportunities totalling $120,000.", fail=None):
        self.requests: list[httpx.Request] = []
        self.answer, self.fail = answer, fail or {}
        self.sessions = 0
        self.expire_next = False
        self.result = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if path == "/services/oauth2/token":
            if self.fail.get("auth"):
                return httpx.Response(400, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "TOKEN-123", "instance_url": DOMAIN})
        assert req.headers["Authorization"] == "Bearer TOKEN-123"
        if path.endswith("/agents/0XxAGENT/sessions") and req.method == "POST":
            self.sessions += 1
            return httpx.Response(200, json={"sessionId": f"sess-{self.sessions}", "messages": []})
        if path.endswith("/messages") and req.method == "POST":
            if self.expire_next:
                self.expire_next = False
                return httpx.Response(404, json={"message": "session not found"})
            if self.fail.get("message"):
                return httpx.Response(500, json={})
            return httpx.Response(200, json={"messages": [
                {"type": "Inform", "id": "m1", "message": self.answer, "isContentSafe": True,
                 "result": self.result}]})
        if req.method == "DELETE":
            return httpx.Response(200, json={"messages": [{"type": "SessionEnded", "reason": "ClientRequest"}]})
        return httpx.Response(404)

    def bodies(self, suffix):
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(suffix) and r.method == "POST"]


def client(fake, secret="s3cr3t"):
    return AgentforceClient(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)),
                            secret_provider=lambda: secret)


# ---------- client: exact Agent API contract ----------
def test_full_flow_matches_agent_api():
    fake = FakeSalesforce()
    r = client(fake).ask("conv-1", "How many open opportunities does Acme have?")
    assert r["answer"].startswith("Acme Corp has 2 open opportunities") and r["content_safe"]

    tok = fake.requests[0]
    assert str(tok.url) == f"{DOMAIN}/services/oauth2/token"
    form = dict(x.split("=") for x in tok.content.decode().split("&"))
    assert form == {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "s3cr3t"}

    start = fake.requests[1]
    assert str(start.url) == f"{API}/agents/0XxAGENT/sessions"
    body = json.loads(start.content)
    assert body["instanceConfig"] == {"endpoint": DOMAIN} and body["bypassUser"] is True
    assert body["streamingCapabilities"] == {"chunkTypes": ["Text"]} and len(body["externalSessionKey"]) == 36

    msg = fake.requests[2]
    assert str(msg.url) == f"{API}/sessions/sess-1/messages"
    assert json.loads(msg.content) == {"message": {"sequenceId": 1, "type": "Text",
                                                   "text": "How many open opportunities does Acme have?"}}


def test_session_reused_and_sequence_increments_per_conversation():
    fake = FakeSalesforce()
    c = client(fake)
    c.ask("conv-1", "q1"); c.ask("conv-1", "q2"); c.ask("conv-2", "other")
    assert fake.sessions == 2                                   # one Agentforce session per conversation
    seqs = [(b["message"]["text"], b["message"]["sequenceId"]) for b in fake.bodies("/messages")]
    assert seqs == [("q1", 1), ("q2", 2), ("other", 1)]
    assert sum(r.url.path.endswith("/oauth2/token") for r in fake.requests) == 1   # token cached


def test_expired_session_is_recreated_once():
    fake = FakeSalesforce()
    c = client(fake)
    c.ask("conv-1", "q1")
    fake.expire_next = True
    r = c.ask("conv-1", "q2")
    assert r["answer"] and fake.sessions == 2
    assert fake.bodies("/messages")[-1]["message"]["sequenceId"] == 1


def test_bypass_user_configurable():
    fake = FakeSalesforce()
    AgentforceClient(dataclasses.replace(CFG, bypass_user=False),
                     http=httpx.Client(transport=httpx.MockTransport(fake)),
                     secret_provider=lambda: "x").ask("c", "q")
    assert fake.bodies("/sessions")[0]["bypassUser"] is False


def test_end_session_sends_delete_with_reason():
    fake = FakeSalesforce()
    c = client(fake)
    c.ask("conv-1", "q1")
    c.end("conv-1")
    d = fake.requests[-1]
    assert d.method == "DELETE" and str(d.url) == f"{API}/sessions/sess-1"
    assert d.headers["x-session-end-reason"] == "UserRequest"


def test_auth_failure_is_safe_error():
    with pytest.raises(SalesforceError) as e:
        client(FakeSalesforce(fail={"auth": True})).ask("c", "q")
    assert "HTTP 400" in str(e.value) and "s3cr3t" not in str(e.value)


def test_config_from_env(monkeypatch):
    for k in ("SALESFORCE_MY_DOMAIN", "SALESFORCE_CLIENT_ID", "SALESFORCE_AGENTFORCE_ID"):
        monkeypatch.delenv(k, raising=False)
    assert SalesforceConfig.from_env() is None
    monkeypatch.setenv("SALESFORCE_MY_DOMAIN", "contoso.my.salesforce.com")
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "cid")
    monkeypatch.setenv("SALESFORCE_AGENTFORCE_ID", "0XxAGENT")
    assert SalesforceConfig.from_env().my_domain == DOMAIN


# ---------- end to end through the agent ----------
@pytest.fixture
def make_agent(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    s = dataclasses.replace(Settings.from_env(), skills_dir=tmp_path / "skills", data_dir=ROOT / "data",
                            fake_model=True)

    def _make(sf):
        return SkillAgent(s, client=FakeClient(), salesforce=sf)
    return _make


def test_agent_routes_to_salesforce_skill_and_calls_agentforce(make_agent):
    fake = FakeSalesforce()
    agent = make_agent(client(fake))
    r = agent.handle("user1:sess", "What open opportunities does the Acme account have?")
    assert r.skill == "salesforce-agentforce" and r.skill_version == "1.1.0"
    assert r.tool_calls[0]["tool"] == "ask_agentforce" and r.tool_calls[0]["status"] == "found"
    assert r.text.startswith("From Salesforce: Acme Corp has 2 open opportunities")
    # only the Salesforce skill's tool was offered
    skill_call = agent.client.chat.completions.calls[1]
    assert [t["function"]["name"] for t in skill_call["tools"]] == ["ask_agentforce"]


def test_agent_reset_ends_agentforce_session(make_agent):
    fake = FakeSalesforce()
    agent = make_agent(client(fake))
    agent.handle("k", "Show the Acme account")
    agent.reset("k")
    assert fake.requests[-1].method == "DELETE"


def test_salesforce_down_is_reported_not_invented(make_agent):
    agent = make_agent(client(FakeSalesforce(fail={"message": True})))
    r = agent.handle("k", "Show the Acme account")
    assert r.tool_calls[0]["status"] == "error" and "Salesforce is unavailable" in r.text


def test_not_configured(make_agent):
    agent = make_agent(False)
    r = agent.handle("k", "Show the Acme account in Salesforce")
    assert r.tool_calls[0]["status"] == "error" and "not configured" in r.text


def test_hr_and_it_unaffected(make_agent):
    agent = make_agent(client(FakeSalesforce()))
    assert agent.handle("a", "What is our vacation policy?").skill == "hr-policy"
    assert agent.handle("b", "Status of INC1001?").skill == "it-ticket"


def test_structured_records_are_returned():
    """Real Agentforce shape (captured from a live org): records live in result[].value.result[]."""
    fake = FakeSalesforce(answer="Here are a few accounts from your Salesforce CRM.")
    fake.result = [{"type": "copilotActionOutput/QueryRecords_x", "property": "", "value": {"result": [
        {"id": "001A", "sObjectInfo": {"apiName": "Account", "label": "Account"}, "title": "Initech",
         "data": {"Id": {"value": "001A", "label": "Account ID"}, "Name": {"value": "Initech", "label": "Account Name"},
                  "Phone": {"value": None, "label": "Account Phone"}}},
        {"id": "001B", "sObjectInfo": {"label": "Account"}, "title": "Acme Corp",
         "data": {"Name": {"value": "Acme Corp", "label": "Account Name"},
                  "AnnualRevenue": {"value": 5000000, "displayValue": "$5,000,000", "label": "Annual Revenue"}}}]}}]
    r = client(fake).ask("c", "List a few accounts")
    assert [x["title"] for x in r["records"]] == ["Initech", "Acme Corp"]
    assert r["records"][0] == {"object": "Account", "id": "001A", "title": "Initech",
                               "fields": {"Account ID": "001A", "Account Name": "Initech"}}   # empty Phone dropped
    assert r["records"][1]["fields"]["Annual Revenue"] == "$5,000,000"                       # displayValue preferred
