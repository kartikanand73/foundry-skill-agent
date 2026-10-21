"""Offline tests: real tools + real runtime, scripted stand-in model (no Azure needed)."""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from agent import SkillAgent
from agent.config import Settings
from agent.fake_llm import FakeClient, _completion
from agent.tools import execute, get_ticket_status, lookup_policy

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


@pytest.fixture
def agent(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    s = dataclasses.replace(Settings.from_env(), skills_dir=tmp_path / "skills", data_dir=DATA, fake_model=True)
    return SkillAgent(s, client=FakeClient())


def calls(agent):
    return agent.client.chat.completions.calls


# ---------- tools ----------
def test_tools_read_json():
    r = lookup_policy(DATA, "vacation")
    assert r["found"] and r["results"][0]["source_id"] == "POL-HR-001"
    assert get_ticket_status(DATA, "INC1002")["status"] == "Resolved"
    assert get_ticket_status(DATA, "INC9999")["found"] is False


def test_tool_arg_validation_and_allowlist():
    res, _ = execute("get_ticket_status", '{"ticket_id": "DROP TABLE"}', ("get_ticket_status",), DATA)
    assert "invalid arguments" in res["error"]
    res, _ = execute("lookup_policy", '{"topic": "vacation"}', ("get_ticket_status",), DATA)
    assert "not permitted" in res["error"]
    res, clean = execute("get_ticket_status", '{"ticket_id": " inc1001 "}', ("get_ticket_status",), DATA)
    assert clean == {"ticket_id": "INC1001"} and res["found"]


# ---------- required demo cases ----------
def test_vacation_policy(agent):
    r = agent.handle("a", "What is our vacation policy?")
    assert r.skill == "hr-policy" and r.skill_version == "1.0.0"
    assert r.tool_calls == [{"tool": "lookup_policy", "args": {"topic": "vacation"}, "status": "found"}]
    assert "15 days" in r.text and "POL-HR-001" in r.text


def test_ticket_status(agent):
    r = agent.handle("a", "What is the status of INC1001?")
    assert r.skill == "it-ticket" and r.tool_calls[0]["status"] == "found"
    assert "In Progress" in r.text and "VPN" in r.text


def test_missing_ticket_id_then_followup(agent):
    r = agent.handle("a", "Check my ticket.")
    assert r.skill == "it-ticket" and r.tool_calls == [] and "ticket ID" in r.text
    r2 = agent.handle("a", "It's INC1003")
    assert r2.tool_calls[0]["args"] == {"ticket_id": "INC1003"} and "Awaiting User" in r2.text


def test_unknown_ticket(agent):
    r = agent.handle("a", "Status of INC9999?")
    assert r.tool_calls[0]["status"] == "not_found" and "couldn't find" in r.text


def test_unrelated(agent):
    r = agent.handle("a", "What's the weather in Columbus?")
    assert r.skill == "unsupported" and "HR policies" in r.text and "IT support ticket" in r.text
    assert len(calls(agent)) == 1  # router only; no skill loaded, no tools offered


def test_both_skills_clarify(agent):
    r = agent.handle("a", "How many holidays do we get and what's up with INC1002?")
    assert r.skill == "clarify" and r.tool_calls == []


# ---------- architecture guarantees ----------
def test_router_sees_catalog_only(agent):
    agent.handle("a", "What is our vacation policy?")
    router_prompt = calls(agent)[0]["messages"][0]["content"]
    assert '"hr-policy"' in router_prompt and '"it-ticket"' in router_prompt
    assert "Cite the policy title" not in router_prompt          # no full skill bodies
    assert "Do not invent details" not in router_prompt
    assert calls(agent)[0]["tools"] is None


def test_only_selected_skill_and_tool_loaded(agent):
    agent.handle("a", "What is the status of INC1001?")
    skill_call = calls(agent)[1]
    assert [t["function"]["name"] for t in skill_call["tools"]] == ["get_ticket_status"]
    assert "Do not invent details" in skill_call["messages"][0]["content"]
    assert "Cite the policy title" not in skill_call["messages"][0]["content"]


def test_previous_skill_does_not_leak(agent):
    agent.handle("a", "What is the status of INC1001?")
    agent.handle("a", "What is our vacation policy?")
    hr_calls = [c for c in calls(agent) if c["tools"] and c["tools"][0]["function"]["name"] == "lookup_policy"]
    for c in hr_calls:
        assert all(m["role"] != "system" or "Do not invent details" not in m["content"] for m in c["messages"])
        assert [t["function"]["name"] for t in c["tools"]] == ["lookup_policy"]


def test_router_choice_validated(agent):
    class Rogue(FakeClient):
        pass
    agent.client = Rogue()
    orig = agent.client.chat.completions.create

    def create(**kw):
        if kw.get("response_format"):
            return _completion({"content": json.dumps({"skill": "delete-all-files", "reason": "x", "clarification": ""})})
        return orig(**kw)
    agent.client.chat.completions.create = create
    r = agent.handle("a", "do something")
    assert r.skill == "unsupported"


def test_bounded_tool_loop(agent):
    """A model that keeps asking for tools is cut off after max_tool_rounds."""
    n = {"tools": 0}

    def create(**kw):
        if kw.get("response_format"):
            return _completion({"content": json.dumps({"skill": "it-ticket", "reason": "", "clarification": ""})})
        if kw.get("tool_choice") == "none":
            return _completion({"content": "stopped"})
        n["tools"] += 1
        return _completion({"content": None, "tool_calls": [{"id": f"c{n['tools']}", "type": "function",
                            "function": {"name": "get_ticket_status", "arguments": '{"ticket_id":"INC1001"}'}}]})
    agent.client.chat.completions.create = create
    r = agent.handle("a", "loop forever")
    assert n["tools"] == agent.s.max_tool_rounds and r.text == "stopped"


def test_model_failure_is_handled(agent):
    def boom(**kw):
        raise TimeoutError("Request timed out")
    agent.client.chat.completions.create = boom
    r = agent.handle("a", "What is our vacation policy?")
    assert r.skill == "error" and "try again" in r.text


def test_history_is_bounded(agent):
    for i in range(20):
        agent.handle("a", f"weather {i}")
    assert len(agent._get_history("a")) == 2 * agent.s.history_turns


def test_hot_reload_without_restart(agent):
    r1 = agent.handle("a", "What is the status of INC1002?")
    assert r1.skill_version == "1.0.0"
    f = agent.s.skills_dir / "it-ticket" / "SKILL.md"
    f.write_text(f.read_text().replace("version: 1.0.0", "version: 1.1.0")
                 + '6. End every answer with: "Need anything else?"\n')
    r2 = agent.handle("b", "What is the status of INC1002?")
    assert r2.skill_version == "1.1.0" and r2.text.endswith("Need anything else?")
