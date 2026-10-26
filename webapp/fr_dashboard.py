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
    banner = (f"**Snapshot** {s['snapshot_at']:%Y-%m-%d %H:%M} UTC ({_ago(s['snapshot_at'], now)}) · "
              f"Salesforce {_ago(s['salesforce_as_of'], now)} · Databricks {_ago(s['databricks_as_of'], now)} · "
              f"Snowflake {_ago(s['snowflake_as_of'], now)}")
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
    k[3].metric("Value short", _money(sm.get("short_value")))
    k[4].metric("SKUs short", sm.get("skus_short", 0))

    st.markdown("#### Customers")
    cust_rows = [{"": STATUS_STYLE.get(c["status"], ("⚪", ""))[0], "Customer": c["customer_name"] or c["customer_id"],
                  "Status": c["status"], "Open orders": c["open_orders"], "At-risk orders": c["at_risk_orders"],
                  "Units short": c["short_units"], "Value short": float(c["short_value"]),
                  "Blocked order value": float(c["blocked_order_value"]), "Worst order": c["worst_order_id"] or "",
                  "Salesforce Id": c["customer_id"]} for c in d["customers"]]
    st.dataframe(cust_rows, hide_index=True, width="stretch", column_config={
        "Value short": st.column_config.NumberColumn(format="$%.2f"),
        "Blocked order value": st.column_config.NumberColumn(format="$%.2f")})

    names = [c["customer_name"] or c["customer_id"] for c in d["customers"]]
    at_risk = [c["customer_name"] or c["customer_id"] for c in d["customers"] if c["status"] != "FULFILLABLE"]
    pick = st.selectbox("Order lines for", ["All at-risk customers"] + names)
    lines = [l for l in d["lines"] if (pick == "All at-risk customers" and l["customer_name"] in at_risk
                                       and l["status"] != "FULFILLABLE") or (l["customer_name"] or l["customer_id"]) == pick]
    st.dataframe([{"": STATUS_STYLE.get(l["status"], ("⚪", ""))[0], "Order": l["order_id"], "Line": l["line_number"],
                   "Customer": l["customer_name"], "SKU": l["product_sku"], "Ordered": str(l["order_date"]),
                   "Qty": l["quantity"], "Allocated": l["allocated_qty"], "Short": l["short_qty"],
                   "Value short": float(l["short_value"]), "Queue #": l["allocation_rank"], "Why": l["reason"]}
                  for l in lines], hide_index=True, width="stretch",
                 column_config={"Value short": st.column_config.NumberColumn(format="$%.2f")})

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
    st.caption(f"Read {_ago(d['loaded_at'], now)} from PostgreSQL view fr.v_fulfillment_risk_current "
               "(cached 60 s). Ask the chat for explanations, e.g. *Why is SO-101996 at risk?*")
