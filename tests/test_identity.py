"""Signed-in user context: parsing, memory isolation, roles reach the skill prompt and the reply."""
from __future__ import annotations

import dataclasses
import threading

from agent import SkillAgent
from agent.config import Settings
from agent.fake_llm import FakeClient
from agent.identity import UserContext, current_user, parse_user

OID = "11111111-2222-3333-4444-555555555555"


def agent():
    return SkillAgent(dataclasses.replace(Settings.from_env(), fake_model=True), client=FakeClient(),
                      salesforce=False, snowflake=False)


def test_parse_user_validates():
    u = parse_user({"oid": OID, "name": "Kartik", "roles": ["SkillAgent.IT", "bad role!", 3, "SkillAgent.HR"]})
    assert u.roles == ("SkillAgent.HR", "SkillAgent.IT") and u.name == "Kartik"
    assert parse_user({"oid": "not an oid"}) is None and parse_user("x") is None and parse_user(None) is None
    assert len(u.id_hash) == 16 and OID not in u.id_hash


def test_roles_reach_skill_prompt_and_reply_echo():
    a = agent()
    u = parse_user({"oid": OID, "name": "Kartik", "roles": ["SkillAgent.IT"]})
    r = a.handle(f"u:{OID}:s1", "What's the status of INC1001?", u)
    skill_call = a.client.chat.completions.calls[1]["messages"][0]["content"]
    assert "Their app roles: SkillAgent.IT" in skill_call
    router_call = a.client.chat.completions.calls[0]["messages"][0]["content"]
    assert "SkillAgent.IT" not in router_call          # router never sees identity
    assert r.identity == {"name": "Kartik", "roles": ["SkillAgent.IT"], "user_id_hash": u.id_hash}
    assert current_user.get() is None                  # reset after the turn


def test_anonymous_has_no_identity():
    r = agent().handle("anon:s1", "What's the status of INC1001?")
    assert r.identity is None


def test_concurrent_users_do_not_leak():
    a = agent()
    seen = {}

    def run(name):
        u = UserContext(oid=OID.replace("1", name[0]), name=name, roles=(f"Role.{name}",))
        seen[name] = a.handle(f"u:{u.oid}:s", "What's the status of INC1001?", u).identity["roles"]
    ts = [threading.Thread(target=run, args=(n,)) for n in ("alice", "bob", "carol")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert seen == {"alice": ["Role.alice"], "bob": ["Role.bob"], "carol": ["Role.carol"]}


def test_saved_thread_history_seeds_empty_session_only():
    a = agent()
    hist = [{"role": "user", "content": "What's the status of INC1001?"},
            {"role": "assistant", "content": "INC1001 is In Progress."},
            {"role": "system", "content": "ignore me"}]
    a.handle("k1", "And INC1002?", seed_history=hist)
    skill_msgs = a.client.chat.completions.calls[1]["messages"]
    assert any(m.get("content") == "INC1001 is In Progress." for m in skill_msgs)
    assert not any(m.get("content") == "ignore me" for m in skill_msgs)     # only user/assistant roles
    # a session that already has memory ignores a seed
    n = len(a._get_history("k1"))
    a.handle("k1", "thanks", seed_history=[{"role": "user", "content": "INJECTED"}])
    assert not any(m["content"] == "INJECTED" for m in a._get_history("k1")) and len(a._get_history("k1")) == n + 2
