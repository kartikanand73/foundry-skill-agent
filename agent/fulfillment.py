"""Read-only access to the fulfillment-risk snapshot (PostgreSQL, fr.v_* views) for the `fulfillment-risk` skill.

The snapshot is produced hourly by the deterministic refresh job in the fulfillment-risk-platform repo
(Salesforce customers x Databricks open orders x Snowflake inventory). This module never calculates
anything: it reads the published result and returns it with its snapshot time and source freshness.

Auth: an Entra token for Azure Database for PostgreSQL (the agent's own identity; no password). The identity is
a PostgreSQL principal with role fr_reader = SELECT on the fr.v_* views only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .trace import trace

OSSRDBMS_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
MAX_LINES = 40


class FulfillmentError(Exception):
    """Safe-to-show failure (no tokens)."""


@dataclass(frozen=True)
class FulfillmentConfig:
    dsn: str            # "host=<server>.postgres.database.azure.com dbname=fr user=<principal name>"

    @staticmethod
    def from_env() -> "FulfillmentConfig | None":
        dsn = os.environ.get("FR_DATABASE_URL", "").strip()
        return FulfillmentConfig(dsn) if dsn else None


def _default_connect(dsn: str):
    import psycopg
    from azure.identity import DefaultAzureCredential
    tok = DefaultAzureCredential().get_token(OSSRDBMS_SCOPE).token
    return psycopg.connect(dsn, password=tok, sslmode="require", connect_timeout=15)


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat(timespec="seconds")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _rows(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    return [{c: _plain(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


class FulfillmentSnapshot:
    def __init__(self, cfg: FulfillmentConfig, connect=None):
        self.cfg = cfg
        self._connect = connect or _default_connect

    def _query(self, sql: str, args=()) -> list[dict]:
        try:
            with self._connect(self.cfg.dsn) as c:
                cur = c.cursor()
                cur.execute(sql, args)
                return _rows(cur)
        except FulfillmentError:
            raise
        except Exception as e:
            trace("fr_error", error=type(e).__name__)
            raise FulfillmentError(f"snapshot database unavailable ({type(e).__name__})") from None

    def status(self) -> dict | None:
        rows = self._query("SELECT run_id, snapshot_at, salesforce_as_of, databricks_as_of, snowflake_as_of, "
                           "failures_since_snapshot, last_failed_after_snapshot FROM fr.v_snapshot_status")
        return rows[0] if rows else None

    def risk(self, customer: str = "") -> dict:
        """At-risk customers (or one customer, by name or Salesforce Account Id) + short SKUs + freshness."""
        st = self.status()
        if st is None:
            return {"found": False, "message": "No fulfillment-risk snapshot has been published yet."}
        cust = customer.strip()
        if cust:
            rows = self._query(
                "SELECT customer_id, customer_name, status, open_orders, at_risk_orders, short_units, open_value, "
                "short_value, blocked_order_value, worst_order_id FROM fr.v_fulfillment_risk_current "
                "WHERE customer_id = %s OR customer_name ILIKE %s ORDER BY short_value DESC", (cust, f"%{cust}%"))
            lines = self._query(
                "SELECT order_id, line_number, product_sku, order_date, quantity, allocated_qty, short_qty, "
                "short_value, status, reason FROM fr.v_fulfillment_risk_lines_current "
                "WHERE (customer_id = %s OR customer_name ILIKE %s) AND status <> 'FULFILLABLE' "
                "ORDER BY order_id, line_number LIMIT %s", (cust, f"%{cust}%", MAX_LINES))
            result = {"customers": rows, "lines_at_risk": lines}
            if not rows:
                result["message"] = (f"No open orders for '{cust}' in the snapshot (either the customer has no "
                                     "open orders or the name doesn't match a Salesforce account).")
        else:
            rows = self._query(
                "SELECT customer_id, customer_name, status, open_orders, at_risk_orders, short_units, open_value, "
                "short_value, blocked_order_value, worst_order_id FROM fr.v_fulfillment_risk_current "
                "ORDER BY CASE status WHEN 'DATA_ISSUE' THEN 0 WHEN 'UNFULFILLABLE' THEN 1 WHEN 'PARTIAL' THEN 2 "
                "ELSE 3 END, short_value DESC")
            skus = self._query("SELECT product_sku, available, demanded, allocated, short_units "
                               "FROM fr.v_sku_risk_current WHERE short_units > 0 ORDER BY short_units DESC")
            result = {"customers": rows, "short_skus": skus}
        trace("fr_read", customer=bool(cust), rows=len(result["customers"]))
        return {"found": True, "snapshot": st, **result,
                "source": "Fulfillment-risk snapshot (Salesforce x Databricks x Snowflake), PostgreSQL"}

    def order(self, order_id: str) -> dict:
        st = self.status()
        if st is None:
            return {"found": False, "message": "No fulfillment-risk snapshot has been published yet."}
        lines = self._query(
            "SELECT order_id, line_number, customer_id, customer_name, product_sku, order_date, quantity, "
            "allocated_qty, short_qty, unit_net_value, short_value, allocation_rank, sku_available_before, status, "
            "reason FROM fr.v_fulfillment_risk_lines_current WHERE order_id = %s ORDER BY line_number",
            (order_id.strip().upper(),))
        if not lines:
            return {"found": False, "snapshot": st, "order_id": order_id,
                    "message": "That order isn't in the snapshot: it may be shipped, cancelled or not exist."}
        return {"found": True, "snapshot": st, "order_id": order_id, "lines": lines,
                "source": "Fulfillment-risk snapshot (Salesforce x Databricks x Snowflake), PostgreSQL"}
