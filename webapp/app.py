"""Streamlit chat UI for the skill agent: threads in the sidebar, each thread = one hosted session.

Browser -> (Entra sign-in, App Service) -> this app -> APIM -> Foundry hosted agent.
Threads live in this browser session's memory (st.session_state).
"""
from __future__ import annotations

import time
import uuid

import streamlit as st

from apim_client import ApimClient, ApimError, principal_from_headers
from thread_store import ThreadStore

SKILL_BADGES = {
    "hr-policy": ("HR policy", ":blue"),
    "it-ticket": ("IT ticket", ":orange"),
    "salesforce-agentforce": ("Salesforce", ":violet"),
    "snowflake-cortex": ("Snowflake", ":green"),
    "clarify": ("Clarifying", ":gray"),
    "unsupported": ("Out of scope", ":gray"),
    "error": ("Error", ":red"),
    "blocked": ("Blocked by safety policy", ":red"),
    "forbidden": ("No access", ":orange"),
}

st.set_page_config(page_title="Skill Agent", page_icon="💬", layout="wide")


# ---------- state ----------
def _new_thread() -> str:
    tid = uuid.uuid4().hex[:12]
    st.session_state.threads[tid] = {"title": "New chat", "session_id": None, "messages": [],
                                     "created": time.time()}
    st.session_state.active = tid
    return tid


@st.cache_resource(show_spinner=False)
def _telemetry():
    """Application Insights via OpenTelemetry, only when the connection string is configured.

    Instruments httpx so every APIM call carries a W3C `traceparent`: APIM and the hosted agent's
    spans then join the same end-to-end trace. Prompt and answer text are not recorded.
    """
    import os
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return None
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry import trace as otel
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        configure_azure_monitor()
        HTTPXClientInstrumentor().instrument()
        return otel.get_tracer("skill_agent.webapp")
    except Exception:   # telemetry must never break the chat
        return None


_tracer = _telemetry()   # before the first httpx client is created


def _span(name: str):
    import contextlib
    return _tracer.start_as_current_span(name) if _tracer else contextlib.nullcontext(None)


@st.cache_resource
def _client() -> ApimClient | None:
    try:
        return ApimClient.from_env()
    except ApimError:
        return None


def _signed_in_user() -> str | None:
    try:  # App Service authentication injects this header after Entra sign-in
        return st.context.headers.get("X-Ms-Client-Principal-Name")
    except Exception:
        return None


def _principal() -> dict | None:
    """Signed-in user from App Service authentication (Easy Auth).

    App Service sets these headers itself after Entra sign-in and drops any a browser sends, so
    they can be trusted here. X-MS-CLIENT-PRINCIPAL = base64 JSON of the ID token's claims.
    """
    try:
        h = st.context.headers
        oid = h.get("X-Ms-Client-Principal-Id")
        if not oid:
            return None
        return principal_from_headers(oid, h.get("X-Ms-Client-Principal-Name"), h.get("X-Ms-Client-Principal"))
    except Exception:
        return None


def _cost_line(u: dict | None) -> str | None:
    if not u:
        return None
    return (f"💲 ${u.get('cost_usd', 0):.4f} model cost · {u.get('input_tokens', 0):,} in / "
            f"{u.get('output_tokens', 0):,} out tokens ({u.get('reasoning_tokens', 0):,} reasoning) · "
            f"{u.get('model_calls', 0)} model calls")


def _thread_cost(t: dict) -> float:
    return sum((m.get("usage") or {}).get("cost_usd", 0) for m in t["messages"])


@st.cache_resource(show_spinner=False)
def _store() -> ThreadStore | None:
    try:
        return ThreadStore.from_env()
    except Exception:
        return None


def _persist(tid: str) -> None:
    """Save one thread for the signed-in user (no-op without a store or a signed-in user)."""
    me, store = _principal(), _store()
    if not (me and store) or tid not in st.session_state.threads:
        return
    try:
        store.save(me["oid"], tid, st.session_state.threads[tid])
    except Exception:
        st.session_state.store_error = True


def _forget(tid: str) -> None:
    me, store = _principal(), _store()
    if me and store:
        try:
            store.delete(me["oid"], tid)
        except Exception:
            st.session_state.store_error = True


# ---------- state: this user's saved threads, else a fresh one ----------
if "threads" not in st.session_state:
    st.session_state.threads = {}
    st.session_state.store_error = False
    _me, _st = _principal(), _store()
    if _me and _st:
        try:
            st.session_state.threads = _st.load(_me["oid"])
        except Exception:
            st.session_state.store_error = True
    if st.session_state.threads:
        st.session_state.active = max(st.session_state.threads,
                                      key=lambda k: st.session_state.threads[k]["created"])
    else:
        _new_thread()


def _badge(skill: str) -> str:
    label, color = SKILL_BADGES.get(skill, (skill or "agent", ":gray"))
    return f"{color}-badge[{label}]"


# ---------- sidebar: threads ----------
with st.sidebar:
    st.title("💬 Skill Agent")
    me = _principal()
    if me:
        st.caption(f"Signed in as {me['name'] or 'unknown'}")
        st.caption("Roles: " + (" · ".join(me["roles"]) if me["roles"] else "none assigned"))
    if st.button("➕ New chat", use_container_width=True):
        _new_thread()
        st.rerun()
    st.divider()
    st.caption("Threads")
    ordered = sorted(st.session_state.threads.items(), key=lambda kv: kv[1]["created"], reverse=True)
    for tid, t in ordered:
        c1, c2 = st.columns([0.82, 0.18])
        is_active = tid == st.session_state.active
        if c1.button(("▸ " if is_active else "") + t["title"], key=f"open_{tid}",
                     use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state.active = tid
            st.rerun()
        with c2.popover("⋯"):
            new_title = st.text_input("Rename", value=t["title"], key=f"title_{tid}")
            if st.button("Save", key=f"save_{tid}"):
                t["title"] = new_title.strip() or t["title"]
                _persist(tid)
                st.rerun()
            if st.button("Delete", key=f"del_{tid}"):
                del st.session_state.threads[tid]
                _forget(tid)
                if not st.session_state.threads:
                    _new_thread()
                elif st.session_state.active == tid:
                    st.session_state.active = max(st.session_state.threads,
                                                  key=lambda k: st.session_state.threads[k]["created"])
                st.rerun()
    st.divider()
    if q := st.session_state.get("quota"):
        st.caption(f"Tokens today: {q['user_today']:,} of {q['user_daily_limit']:,}")
        st.progress(min(q["user_today"] / max(q["user_daily_limit"], 1), 1.0))
        if q.get("ledger") not in ("ok", ""):
            st.caption(f"Token ledger: {q['ledger']}")
    total = sum(_thread_cost(t) for t in st.session_state.threads.values())
    if total > 0:
        st.caption(f"💲 All threads this session: ${total:.4f} model cost")
    if _store() and _principal():
        st.caption("Your threads are saved for 30 days.")
    else:
        st.caption("Threads are kept for this browser session only.")
    if st.session_state.get("store_error"):
        st.caption("⚠️ Couldn't reach thread storage; this session's threads may not be saved.")
    st.caption("Ask about HR policies, IT tickets, Salesforce accounts or Snowflake claims metrics.")


# ---------- main: active thread ----------
thread = st.session_state.threads[st.session_state.active]
st.subheader(thread["title"])
if (spent := _thread_cost(thread)) > 0:
    st.caption(f"This thread so far: ${spent:.4f} model cost")

for m in thread["messages"]:
    with st.chat_message(m["role"]):
        if m["role"] == "assistant" and m.get("skill"):
            st.markdown(_badge(m["skill"]))
        st.markdown(m["content"])
        if line := _cost_line(m.get("usage")):
            st.caption(line)
        if m.get("tool_calls") or m.get("identity"):
            with st.expander("Details"):
                if m.get("identity"):
                    st.caption("Identity the agent received")
                    st.json(m["identity"])
                if m.get("tool_calls"):
                    st.json(m["tool_calls"])

prompt = st.chat_input("Ask the agent…")
if prompt:
    if not thread["messages"]:
        thread["title"] = (prompt[:40] + "…") if len(prompt) > 40 else prompt
    thread["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    client = _client()
    with st.chat_message("assistant"):
        if client is None:
            msg = {"role": "assistant", "content": "The app isn't configured to reach the agent yet.",
                   "skill": "error"}
        else:
            with st.spinner("Thinking…"), _span("webapp.chat_turn") as sp:
                if sp:
                    sp.set_attribute("webapp.thread", st.session_state.active)
                    sp.set_attribute("webapp.follow_up", bool(thread["session_id"]))
                    sp.set_attribute("webapp.signed_in", bool(_principal()))
                try:
                    prior = [{"role": m["role"], "content": m["content"]} for m in thread["messages"][:-1]
                             if m.get("skill") != "error"][-12:]
                    res = client.chat(prompt, session_id=thread["session_id"], user=_principal(), history=prior)
                    thread["session_id"] = res.session_id
                    if res.quota:
                        st.session_state.quota = res.quota
                    msg = {"role": "assistant", "content": res.text or "_(no answer)_", "skill": res.skill,
                           "tool_calls": res.tool_calls, "usage": res.usage, "identity": res.identity}
                except ApimError as e:
                    msg = {"role": "assistant", "content": f"⚠️ {e}", "skill": "error"}
                if sp:
                    sp.set_attribute("webapp.skill", msg["skill"])
                    sp.set_attribute("webapp.agent_session", thread["session_id"] or "")
                    sp.set_attribute("webapp.tools", ",".join(c.get("tool", "") for c in msg.get("tool_calls") or []))
        st.markdown(_badge(msg["skill"]))
        st.markdown(msg["content"])
        if line := _cost_line(msg.get("usage")):
            st.caption(line)
        if msg.get("tool_calls") or msg.get("identity"):
            with st.expander("Details"):
                if msg.get("identity"):
                    st.caption("Identity the agent received")
                    st.json(msg["identity"])
                if msg.get("tool_calls"):
                    st.json(msg["tool_calls"])
    thread["messages"].append(msg)
    _persist(st.session_state.active)
    st.rerun()
