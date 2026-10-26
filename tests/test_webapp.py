"""Streamlit web app + APIM client tests (no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

WEB = Path(__file__).resolve().parent.parent / "webapp"
sys.path.insert(0, str(WEB))

import apim_client  # noqa: E402
from apim_client import ApimClient, ApimError  # noqa: E402

BASE = "https://apim-x.azure-api.net/skill-agent"


# ---------- APIM client ----------
def make(handler):
    return ApimClient(BASE, "KEY", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_first_call_has_no_session_and_returns_assigned_one():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, headers={"x-agent-session-id": "sess-A"},
                              json={"response": "hi", "skill": "hr-policy", "tool_calls": []})
    r = make(h).chat("hello", user="kartik@x.com")
    q = seen[0]
    assert str(q.url) == f"{BASE}/chat" and q.headers["Ocp-Apim-Subscription-Key"] == "KEY"
    assert q.headers["x-client-user"] == "kartik@x.com" and json.loads(q.content) == {"message": "hello"}
    assert r.session_id == "sess-A" and r.skill == "hr-policy"


def test_bearer_token_sent_when_provider_set():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"response": "ok", "skill": "it-ticket"})
    c = ApimClient(BASE, "KEY", http=httpx.Client(transport=httpx.MockTransport(h)), token_provider=lambda: "TOK")
    c.chat("x")
    assert seen[0].headers["Authorization"] == "Bearer TOK" and seen[0].headers["Ocp-Apim-Subscription-Key"] == "KEY"


def test_no_bearer_without_provider():
    seen = []
    make(lambda req: seen.append(req) or httpx.Response(200, json={})).chat("x")
    assert "Authorization" not in seen[0].headers


def test_token_failure_is_friendly():
    def boom():
        raise RuntimeError("IMDS down, secret=abc")
    c = ApimClient(BASE, "KEY", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
                   token_provider=boom)
    with pytest.raises(ApimError) as e:
        c.chat("x")
    assert "sign-in token" in str(e.value) and "abc" not in str(e.value)


def test_follow_up_sends_session():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"response": "ok", "skill": "it-ticket"})
    r = make(h).chat("again", session_id="sess-A")
    assert seen[0].url.params["agent_session_id"] == "sess-A" and r.session_id == "sess-A"


@pytest.mark.parametrize("status,phrase", [(429, "Too many"), (401, "credentials"), (502, "HTTP 502")])
def test_errors_are_friendly(status, phrase):
    with pytest.raises(ApimError) as e:
        make(lambda req: httpx.Response(status, json={})).chat("x")
    assert phrase in str(e.value) and "KEY" not in str(e.value)


def test_timeout():
    def h(req):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(ApimError, match="too long"):
        make(h).chat("x")


def test_not_configured():
    with pytest.raises(ApimError, match="configured"):
        ApimClient("", "")


# ---------- Streamlit UI: threads ----------
@pytest.fixture
def app(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("APIM_BASE_URL", BASE)
    monkeypatch.setenv("APIM_SUBSCRIPTION_KEY", "KEY")
    calls = []
    counter = {"n": 0}

    def fake_chat(self, message, session_id=None, user=None):
        calls.append((message, session_id))
        if session_id is None:
            counter["n"] += 1
            session_id = f"sess-{counter['n']}"
        return apim_client.ChatResult(text=f"answer to {message}", skill="hr-policy", session_id=session_id,
                                      usage={"cost_usd": 0.0123, "input_tokens": 4000, "output_tokens": 900,
                                             "reasoning_tokens": 500, "model_calls": 3})
    monkeypatch.setattr(apim_client.ApimClient, "chat", fake_chat)
    at = AppTest.from_file(str(WEB / "app.py"), default_timeout=30)
    at.run()
    return at, calls


def _thread_buttons(at):
    return [b for b in at.sidebar.button if b.key and b.key.startswith("open_")]


def test_threads_keep_separate_sessions(app):
    at, calls = app
    at.chat_input[0].set_value("What is our vacation policy?").run()
    at.chat_input[0].set_value("And holidays?").run()
    # follow-up in thread 1 reused its session
    assert calls == [("What is our vacation policy?", None), ("And holidays?", "sess-1")]
    t1 = at.session_state.active
    assert at.session_state.threads[t1]["title"] == "What is our vacation policy?"
    assert len(at.session_state.threads[t1]["messages"]) == 4

    # new thread gets a fresh session
    at.sidebar.button[0].click().run()            # "New chat"
    at.chat_input[0].set_value("Status of INC1001?").run()
    assert calls[-1] == ("Status of INC1001?", None)
    t2 = at.session_state.active
    assert t2 != t1 and at.session_state.threads[t2]["session_id"] == "sess-2"
    assert len(_thread_buttons(at)) == 2

    # switch back to thread 1 and continue: uses sess-1 again
    next(b for b in _thread_buttons(at) if b.key == f"open_{t1}").click().run()
    assert at.session_state.active == t1
    at.chat_input[0].set_value("One more").run()
    assert calls[-1] == ("One more", "sess-1")
    assert not at.exception


def test_error_shown_not_crash(app, monkeypatch):
    at, _ = app

    def boom(self, message, session_id=None, user=None):
        raise ApimError("Too many requests right now - please wait a few seconds and retry.")
    monkeypatch.setattr(apim_client.ApimClient, "chat", boom)
    at.chat_input[0].set_value("hello").run()
    msgs = at.session_state.threads[at.session_state.active]["messages"]
    assert msgs[-1]["skill"] == "error" and "Too many" in msgs[-1]["content"] and not at.exception


def test_cost_shown_per_answer_and_thread(app):
    at, _ = app
    at.chat_input[0].set_value("q1").run()
    at.chat_input[0].set_value("q2").run()
    captions = [c.value for c in at.caption]
    assert sum("$0.0123 model cost" in c for c in captions) == 2
    assert any("This thread so far: $0.0246" in c for c in captions)
    assert any("All threads this session: $0.0246" in c for c in captions)


def test_usage_parsed_from_apim():
    r = make(lambda req: httpx.Response(200, json={"response": "x", "skill": "it-ticket",
                                                   "usage": {"cost_usd": 0.01}})).chat("q")
    assert r.usage == {"cost_usd": 0.01}
