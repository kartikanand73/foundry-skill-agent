"""Fulfillment-risk dashboard screen. Reads the published snapshot only (PostgreSQL fr.v_* views, role fr_reader)
with the web app's managed identity - no passwords. Never queries Salesforce, Databricks or Snowflake and never
recalculates: the hourly refresh job (fulfillment-risk-platform repo) owns the numbers."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

REQUIRED_ROLES = ("SkillAgent.FulfillmentRisk", "SkillAgent.Admin")
OSSRDBMS_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
REV_HELP = ("Revenue at risk = units that can't be allocated × the line's net unit price (after discount). "
            "The prorated value of the shortage only.")
BLOCKED_HELP = ("Blocked order value = the FULL value of every order that can't ship complete, including lines "
                "that are in stock. What's held up if you only ship complete orders.")
FIFO_RULE = ("Inventory allocated by order date, then customer tier, then order ID "
             "(oldest order first; stock pooled across locations, reserved units excluded).")
SOURCES = (("Salesforce", "salesforce"), ("Databricks", "databricks"), ("Snowflake", "snowflake"))
HEALTH = {"ok": "🟢", "aging": "🟡", "stale": "🔴", "failing": "🔴"}
STATUS_STYLE = {"UNFULFILLABLE": ("🔴", ":red"), "PARTIAL": ("🟠", ":orange"), "DATA_ISSUE": ("🟣", ":violet"),
                "FULFILLABLE": ("🟢", ":green")}


def allowed(principal: dict | None) -> bool:
    return bool(principal) and any(r in principal.get("roles", []) for r in REQUIRED_ROLES)


def _connect():
    import psycopg
    from azure.identity import DefaultAzureCredential
    dsn = os.environ.get("FR_DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("FR_DATABASE_URL is not set")
    tok = DefaultAzureCredential().get_token(OSSRDBMS_SCOPE).token
    return psycopg.connect(dsn, password=tok, sslmode="require", connect_timeout=15)


def _q(sql: str, args=()) -> list[dict]:
    with _connect() as c:
        cur = c.execute(sql, args)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@st.cache_data(ttl=60, show_spinner=False)
def load() -> dict:
    """Everything the screen needs, in one cached read (60 s): status, customers, lines at risk, skus, runs."""
    status = _q("SELECT * FROM fr.v_snapshot_status")
    return {
        "status": status[0] if status else None,
        "customers": _q("SELECT * FROM fr.v_fulfillment_risk_current ORDER BY CASE status WHEN 'DATA_ISSUE' THEN 0 "
                        "WHEN 'UNFULFILLABLE' THEN 1 WHEN 'PARTIAL' THEN 2 ELSE 3 END, short_value DESC"),
        "lines": _q("SELECT order_id, line_number, customer_id, customer_name, product_sku, order_date, quantity, "
                    "allocated_qty, short_qty, short_value, allocation_rank, sku_available_before, status, reason "
                    "FROM fr.v_fulfillment_risk_lines_current ORDER BY customer_name, order_id, line_number"),
        "skus": _q("SELECT product_sku, available, demanded, allocated, short_units FROM fr.v_sku_risk_current "
                   "ORDER BY short_units DESC, product_sku"),
        "runs": _q("SELECT run_id, status, trigger, started_at, duration_s, customers_at_risk, short_value, error, "
                   "is_published FROM fr.v_refresh_history ORDER BY run_id DESC LIMIT 24"),
        "loaded_at": datetime.now(timezone.utc),
    }


def _ago(t: datetime | None, now: datetime) -> str:
    if not t:
        return "—"
    m = int((now - t).total_seconds() // 60)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago"


def _money(v) -> str:
    return f"${Decimal(v or 0):,.2f}"


def freshness_level(status: dict, now: datetime, max_age_h: float = 24.0) -> str:
    """ok | stale | failing - drives the banner colour."""
    if status.get("failures_since_snapshot"):
        return "failing"
    oldest = min(t for t in (status["salesforce_as_of"], status["databricks_as_of"], status["snowflake_as_of"]) if t)
    return "stale" if (now - oldest).total_seconds() > max_age_h * 3600 or \
        (now - status["snapshot_at"]).total_seconds() > 2 * 3600 else "ok"


def source_health(key: str, as_of: datetime | None, now: datetime, runs: list[dict], fresh_h: float = 2.0,
                  max_age_h: float = 24.0) -> str:
    """ok | aging | stale | failing for one source.
    failing = the latest refresh failed and its error names this source (the snapshot keeps the last good data);
    stale = older than the publish gate (24 h); aging = older than ~2 hourly cycles."""
    latest = runs[0] if runs else None
    if latest and latest["status"] in ("failed", "abandoned") and key in (latest.get("error") or "").lower():
        return "failing"
    if not as_of:
        return "stale"
    age_h = (now - as_of).total_seconds() / 3600
    return "stale" if age_h > max_age_h else "aging" if age_h > fresh_h else "ok"


def ask_prompt(order_id: str) -> str:
    return f"Why is order {order_id} at risk?"


def _ask_about(order_id: str) -> None:
    """Button callback: switch to the chat screen with the question queued (callbacks may set widget state)."""
    st.session_state.pending_prompt = ask_prompt(order_id)
    st.session_state.screen = "💬 Chat"


SIGNAL_LABEL = {"inventory.adjusted": "📦 Inventory adjusted", "stock.received": "🚚 Stock received",
                "order.status_changed": "🔁 Order status", "shipment.delayed": "⏰ Shipment delayed"}
CONTEXT_NOTE = ("Signals are context only. They never change the numbers above; fulfillment risk updates at the "
                "next hourly refresh from Salesforce, Databricks and Snowflake.")


def load_signals() -> dict:
    """Live (uncached) read of the signal views; the section refreshes itself every 15 s."""
    s = _q("SELECT * FROM fr.v_signal_summary")
    return {"summary": s[0] if s else {},
            "recent": _q("SELECT * FROM fr.v_signal_recent ORDER BY received_at DESC LIMIT 25"),
            "rejected": _q("SELECT reason, detail, raw_preview, received_at FROM fr.v_signal_rejected_recent "
                           "ORDER BY received_at DESC LIMIT 15")}


def signal_detail(r: dict) -> str:
    p = r.get("payload") or {}
    t = r["event_type"]
    if t == "inventory.adjusted":
        return f"{r['product_sku']} @ {r['location']}: {p.get('delta', 0):+d} ({p.get('reason', '')})"
    if t == "stock.received":
        return f"{r['product_sku']} @ {r['location']}: +{p.get('quantity')} received"
    if t == "order.status_changed":
        return f"{r['order_id']}: {p.get('from_status')} → {p.get('to_status')}"
    if t == "shipment.delayed":
        return f"{r['order_id']}: now expected {p.get('expected_date')} ({p.get('reason', '')})"
    return r.get("entity_key", "")


def snapshot_context(r: dict) -> str:
    if r.get("order_id"):
        st_ = r.get("snapshot_order_status")
        return f"order {st_}" if st_ else "order not open in snapshot"
    if r.get("product_sku"):
        su = r.get("snapshot_sku_short_units")
        return f"SKU short {su} in snapshot" if su else "SKU not short in snapshot"
    return ""


def _signals_body() -> None:
    try:
        g = load_signals()
    except Exception as e:
        st.warning(f"Live signals unavailable right now ({type(e).__name__}).")
        return
    sm, now = g["summary"], datetime.now(timezone.utc)
    k = st.columns(5)
    k[0].metric("Events (1 h)", sm.get("accepted_1h", 0), help="Accepted events in the last hour (24 h: "
                f"{sm.get('accepted_24h', 0)})")
    k[1].metric("Rejected (1 h)", sm.get("rejected_1h", 0), help="Invalid events (bad JSON, unknown type, bad ids, "
                f"future/stale timestamps). 24 h: {sm.get('rejected_24h', 0)}")
    k[2].metric("Duplicates (1 h)", sm.get("duplicates_1h", 0), help="Redeliveries of an event_id already stored - "
                "kept once (idempotent).")
    k[3].metric("Late (1 h)", sm.get("late_1h", 0), help="Arrived after a newer event for the same order or "
                "SKU@location; stored and flagged, does not replace the newer one.")
    lag = sm.get("lag_p50_s_1h")
    k[4].metric("Pipeline lag p50", f"{float(lag):.1f}s" if lag is not None else "—",
                help="Event Hubs enqueue → stored in PostgreSQL, median over the last hour"
                     + (f" (p95 {float(sm['lag_p95_s_1h']):.1f}s)" if sm.get("lag_p95_s_1h") is not None else ""))
    rows = g["recent"]
    if rows:
        st.dataframe([{"Received": _ago(r["received_at"], now), "Event": SIGNAL_LABEL.get(r["event_type"], r["event_type"]),
                       "Customer": r.get("customer_name") or r.get("customer_id") or "",
                       "Detail": signal_detail(r), "Snapshot says": snapshot_context(r),
                       "Flags": " ".join(x for x in (("⏪ late" if r["is_late"] else ""),
                                                     (f"♻️ ×{r['duplicate_count'] + 1}" if r["duplicate_count"] else ""),
                                                     ("🆕 after snapshot" if r.get("after_snapshot") else "")) if x),
                       "Source": r["source"]} for r in rows], hide_index=True, width="stretch",
                     column_config={"Snapshot says": st.column_config.TextColumn(
                         help="Status in the current fulfillment-risk snapshot - unchanged by this event"),
                         "Flags": st.column_config.TextColumn(
                             help="⏪ late = older than the newest event for the same entity · ♻️ duplicate "
                                  "deliveries · 🆕 arrived after the snapshot was taken (not reflected yet)")})
    else:
        st.caption("No events in the last 7 days.")
    if g["rejected"]:
        with st.expander(f"Rejected events ({len(g['rejected'])} most recent)"):
            st.dataframe([{"Received": _ago(r["received_at"], now), "Reason": r["reason"], "Detail": r["detail"],
                           "Body (start)": r["raw_preview"]} for r in g["rejected"]], hide_index=True, width="stretch")
    st.caption(f"Live · refreshed {now:%H:%M:%S} UTC · Event Hubs → Azure Function → PostgreSQL")


@st.fragment(run_every="15s")
def signals_section() -> None:
    st.markdown("#### ⚡ Live operational signals")
    st.caption(f"ℹ️ {CONTEXT_NOTE}")
    _signals_body()


def render(principal: dict | None) -> None:
    st.subheader("📦 Fulfillment risk")
    st.caption("Which Salesforce customers have open Databricks orders that current Snowflake inventory can't "
               "fulfil? Read-only view of the hourly snapshot; stock is allocated oldest order first.")
    if not allowed(principal):
        st.warning("You need the **SkillAgent.FulfillmentRisk** role to see this screen. Ask your administrator.")
        return
    c1, c2 = st.columns([0.85, 0.15])
    if c2.button("↻ Refresh view", help="Re-read the snapshot (the data itself refreshes hourly)"):
        load.clear()
    try:
        with st.spinner("Reading snapshot…"):
            d = load()
    except Exception as e:
        st.error(f"The snapshot database is unavailable right now ({type(e).__name__}).")
        return
    s = d["status"]
    if not s:
        st.info("No snapshot has been published yet.")
        return
    now = datetime.now(timezone.utc)
    level = freshness_level(s, now)
    health = {key: source_health(key, s[f"{key}_as_of"], now, d["runs"]) for _, key in SOURCES}
    banner = (f"**Snapshot** {s['snapshot_at']:%Y-%m-%d %H:%M} UTC ({_ago(s['snapshot_at'], now)})  \n" +
              " · ".join(f"{HEALTH[health[key]]} {label} {_ago(s[f'{key}_as_of'], now)}"
                         + (" (last refresh failed here)" if health[key] == "failing" else "")
                         for label, key in SOURCES))
    if level == "failing":
        c1.error(banner + f"  \n⚠️ {s['failures_since_snapshot']} refresh(es) failed since this snapshot; "
                          "showing the last successful one.")
    elif level == "stale":
        c1.warning(banner + "  \n⚠️ This snapshot is older than expected.")
    else:
        c1.success(banner)

    sm = s["summary"] or {}
    k = st.columns(5)
    k[0].metric("Customers at risk", f"{sm.get('customers_at_risk', 0)} of {sm.get('customers_with_open_orders', 0)}")
    k[1].metric("Orders at risk", f"{sm.get('at_risk_orders', 0)} of {sm.get('open_orders', 0)}")
    k[2].metric("Units short", f"{sm.get('short_units', 0):,}")
    k[3].metric("Revenue at risk", _money(sm.get("short_value")), help=REV_HELP)
    k[4].metric("SKUs short", sm.get("skus_short", 0))

    st.markdown("#### Customers")
    cust_rows = [{"": STATUS_STYLE.get(c["status"], ("⚪", ""))[0], "Customer": c["customer_name"] or c["customer_id"],
                  "Status": c["status"], "Open orders": c["open_orders"], "At-risk orders": c["at_risk_orders"],
                  "Units short": c["short_units"], "Revenue at risk": float(c["short_value"]),
                  "Blocked order value": float(c["blocked_order_value"]), "Worst order": c["worst_order_id"] or "",
                  "Salesforce Id": c["customer_id"]} for c in d["customers"]]
    st.dataframe(cust_rows, hide_index=True, width="stretch", column_config={
        "Revenue at risk": st.column_config.NumberColumn(format="$%.2f", help=REV_HELP),
        "Blocked order value": st.column_config.NumberColumn(format="$%.2f", help=BLOCKED_HELP)})

    names = [c["customer_name"] or c["customer_id"] for c in d["customers"]]
    at_risk = [c["customer_name"] or c["customer_id"] for c in d["customers"] if c["status"] != "FULFILLABLE"]
    st.markdown("#### Order lines")
    st.info(f"ℹ️ {FIFO_RULE}")
    pick = st.selectbox("Order lines for", ["All at-risk customers"] + names)
    lines = [l for l in d["lines"] if (pick == "All at-risk customers" and l["customer_name"] in at_risk
                                       and l["status"] != "FULFILLABLE") or (l["customer_name"] or l["customer_id"]) == pick]
    event = st.dataframe(
        [{"": STATUS_STYLE.get(l["status"], ("⚪", ""))[0], "Order": l["order_id"], "Line": l["line_number"],
          "Customer": l["customer_name"], "SKU": l["product_sku"], "Ordered": str(l["order_date"]),
          "Qty": l["quantity"], "Allocated": l["allocated_qty"], "Short": l["short_qty"],
          "Revenue at risk": float(l["short_value"]), "Queue #": l["allocation_rank"], "Why": l["reason"]}
         for l in lines], hide_index=True, width="stretch", key="fr_lines", on_select="rerun",
        selection_mode="single-row",
        column_config={"Revenue at risk": st.column_config.NumberColumn(format="$%.2f", help=REV_HELP),
                       "Queue #": st.column_config.NumberColumn(help="Position in the allocation queue (1 = served "
                                                                     "first). " + FIFO_RULE)})
    rows = (getattr(getattr(event, "selection", None), "rows", None) or []) if event is not None else []
    if rows and rows[0] < len(lines):
        oid = lines[rows[0]]["order_id"]
        st.button(f"💬 Ask the agent: “{ask_prompt(oid)}”", key="fr_ask", type="primary", on_click=_ask_about,
                  args=(oid,), help="Opens Chat with this question; answered from the same snapshot.")
    else:
        st.caption("Select an order line to ask the agent why that order is at risk.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Short SKUs")
        short = [x for x in d["skus"] if x["short_units"] > 0]
        if short:
            st.dataframe([{"SKU": x["product_sku"], "Available": x["available"], "Demanded": x["demanded"],
                           "Allocated": x["allocated"], "Short": x["short_units"]} for x in short],
                         hide_index=True, width="stretch")
            st.bar_chart({x["product_sku"]: x["short_units"] for x in short}, horizontal=True, height=180)
        else:
            st.caption("No SKU is short.")
    with right:
        st.markdown("#### Refresh history")
        st.dataframe([{"Run": r["run_id"], "Status": ("✅ " if r["status"] == "succeeded" else "❌ ") + r["status"]
                       + (" · published" if r["is_published"] else ""), "Trigger": r["trigger"],
                       "Started (UTC)": f"{r['started_at']:%m-%d %H:%M}", "Secs": float(r["duration_s"] or 0),
                       "At risk": r["customers_at_risk"], "Error": r["error"] or ""} for r in d["runs"]],
                     hide_index=True, width="stretch", height=260)
    st.divider()
    signals_section()
    st.caption(f"Read {_ago(d['loaded_at'], now)} from PostgreSQL view fr.v_fulfillment_risk_current "
               "(cached 60 s).")
