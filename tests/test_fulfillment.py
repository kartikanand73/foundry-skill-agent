"""fulfillment-risk skill: snapshot reader (fake DB connection), tools, routing, role gate. No network."""
from __future__ import annotations

import dataclasses
import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agent import SkillAgent
from agent.config import Settings
from agent.fake_llm import FakeClient
from agent.fulfillment import FulfillmentConfig, FulfillmentSnapshot
from agent.guardrails import authorize
from agent.identity import UserContext
from agent.tools import ToolContext, execute

ROOT = Path(__file__).resolve().parent.parent
T = datetime(2026, 10, 26, 20, 7, 28, tzinfo=timezone.utc)
STATUS = [("run_id", 2), ("snapshot_at", T), ("salesforce_as_of", T), ("databricks_as_of", T),
          ("snowflake_as_of", T), ("failures_since_snapshot", 0), ("last_failed_after_snapshot", None)]
CUSTS = [{"customer_id": "001fj000026l3pEAAQ", "customer_name": "Globex Inc", "status": "UNFULFILLABLE",
          "open_orders": 3, "at_risk_orders": 1, "short_units": 39, "open_value": Decimal("50065.95"),
          "short_value": Decimal("20253.40"), "blocked_order_value": Decimal("28447.00"), "worst_order_id": "SO-101996"},
         {"customer_id": "001fj000026m1nLAAQ", "customer_name": "Initech", "status": "PARTIAL",
          "open_orders": 5, "at_risk_orders": 1, "short_units": 16, "open_value": Decimal("109626.50"),
          "short_value": Decimal("20000.00"), "blocked_order_value": Decimal("66922.00"), "worst_order_id": "SO-101991"}]
LINE = {"order_id": "SO-101996", "line_number": 1, "customer_id": "001fj000026l3pEAAQ", "customer_name": "Globex Inc",
        "product_sku": "P-1003", "order_date": datetime(2026, 10, 25).date(), "quantity": 23, "allocated_qty": 0,
        "short_qty": 23, "unit_net_value": Decimal("833.00"), "short_value": Decimal("19159.00"), "allocation_rank": 40,
        "sku_available_before": 0, "status": "UNFULFILLABLE", "reason": "no P-1003 left (0 available before this line)"}


class Col:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, db):
        self.db, self.description, self._rows = db, None, []

    def execute(self, sql, args=()):
        self.db.sql.append((sql, args))
        if self.db.fail:
            raise OSError("connection refused")
        if "v_snapshot_status" in sql:
            rows = [dict(STATUS)] if self.db.published else []
        elif "v_fulfillment_risk_current" in sql:
            rows = [c for c in CUSTS if not args or args[0] == c["customer_id"] or args[1].strip("%").lower()
                    in c["customer_name"].lower()]
        elif "v_sku_risk_current" in sql:
            rows = [{"product_sku": "P-1003", "available": 0, "demanded": 23, "allocated": 0, "short_units": 23}]
        elif "v_fulfillment_risk_lines_current" in sql:
            rows = [LINE] if (not args or "SO-101996" in args or args[0] == LINE["customer_id"]
                              or (len(args) > 1 and args[1].strip("%").lower() in LINE["customer_name"].lower())) else []
        else:
            rows = []
        cols = list(rows[0]) if rows else ["x"]
        self.description = [Col(c) for c in cols]
        self._rows = [tuple(r[c] for c in cols) for r in rows]

    def fetchall(self):
        return self._rows


class FakeDB:
    def __init__(self, published=True, fail=False):
        self.published, self.fail, self.sql, self.dsns = published, fail, [], []

    def connect(self, dsn):
        self.dsns.append(dsn)
        db = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return FakeCursor(db)
        return Conn()


def snap(db):
    return FulfillmentSnapshot(FulfillmentConfig("host=x dbname=fr user=skill-agent"), connect=db.connect)


def test_risk_all_customers_has_freshness_and_short_skus():
    db = FakeDB()
    r = snap(db).risk("")
    assert r["found"] and [c["customer_name"] for c in r["customers"]] == ["Globex Inc", "Initech"]
    assert r["snapshot"]["snapshot_at"] == "2026-10-26T20:07:28+00:00" and r["short_skus"][0]["product_sku"] == "P-1003"
    assert r["customers"][0]["short_value"] == 20253.4                       # JSON-friendly numbers
    assert all(q.lstrip().upper().startswith("SELECT") for q, _ in db.sql)  # read-only
    assert all(" fr.v_" in q for q, _ in db.sql)                            # views only, never base tables


def test_risk_one_customer_by_name():
    r = snap(FakeDB()).risk("globex")
    assert [c["customer_name"] for c in r["customers"]] == ["Globex Inc"] and r["lines_at_risk"][0]["order_id"] == "SO-101996"


def test_order_and_missing_order():
    s = snap(FakeDB())
    assert s.order("so-101996")["lines"][0]["reason"].startswith("no P-1003")
    miss = s.order("SO-100001")
    assert not miss["found"] and "shipped" in miss["message"]


def test_no_snapshot_yet_and_db_down():
    assert not snap(FakeDB(published=False)).risk("")["found"]
    ctx = ToolContext(ROOT / "data", fulfillment=snap(FakeDB(fail=True)))
    r, _ = execute("get_fulfillment_risk", '{"customer": ""}', ("get_fulfillment_risk",), ctx)
    assert r["error"].startswith("Fulfillment risk: snapshot database unavailable (OSError)")


def test_tool_argument_validation():
    ctx = ToolContext(ROOT / "data", fulfillment=snap(FakeDB()))
    bad, _ = execute("get_order_fulfillment", '{"order_id": "DROP TABLE"}', ("get_order_fulfillment",), ctx)
    assert "invalid arguments" in bad["error"]
    ok, clean = execute("get_order_fulfillment", '{"order_id": " so-101996 "}', ("get_order_fulfillment",), ctx)
    assert clean == {"order_id": "SO-101996"} and ok["found"]


def test_from_env(monkeypatch):
    monkeypatch.delenv("FR_DATABASE_URL", raising=False)
    assert FulfillmentConfig.from_env() is None
    monkeypatch.setenv("FR_DATABASE_URL", "host=h dbname=fr user=skill-agent")
    assert FulfillmentConfig.from_env().dsn.endswith("user=skill-agent")


# ---------- through the agent ----------
@pytest.fixture
def make_agent(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    s = dataclasses.replace(Settings.from_env(), skills_dir=tmp_path / "skills", data_dir=ROOT / "data",
                            fake_model=True)
    return lambda f: SkillAgent(s, client=FakeClient(), salesforce=False, snowflake=False, genie=False, fulfillment=f)


def test_routes_and_answers_with_freshness(make_agent):
    agent = make_agent(snap(FakeDB()))
    r = agent.handle("k", "Which customers have orders at risk because of inventory?")
    assert r.skill == "fulfillment-risk" and r.tool_calls[0]["tool"] == "get_fulfillment_risk"
    assert r.text.startswith("From the fulfillment-risk snapshot:") and "Globex Inc: UNFULFILLABLE" in r.text
    assert "Snowflake 2026-10-26T20:07:28+00:00" in r.text
    offered = sorted(t["function"]["name"] for t in agent.client.chat.completions.calls[1]["tools"])
    assert offered == ["get_fulfillment_risk", "get_order_fulfillment"]


def test_order_question_uses_order_tool(make_agent):
    r = make_agent(snap(FakeDB())).handle("k", "Why is SO-101996 at risk for fulfillment?")
    assert r.tool_calls[0]["tool"] == "get_order_fulfillment" and "P-1003: UNFULFILLABLE" in r.text


def test_not_configured_and_down(make_agent):
    assert "not configured" in make_agent(False).handle("k", "Show fulfillment risk").text
    assert "unavailable" in make_agent(snap(FakeDB(fail=True))).handle("k", "Show fulfillment risk").text


def test_role_required():
    u = UserContext(oid="o", name="n", roles=("SkillAgent.User", "SkillAgent.Orders"))
    assert "SkillAgent.FulfillmentRisk" in authorize(u, "SkillAgent.FulfillmentRisk", "fulfillment-risk", True)
    ok = UserContext(oid="o", name="n", roles=("SkillAgent.User", "SkillAgent.FulfillmentRisk"))
    assert authorize(ok, "SkillAgent.FulfillmentRisk", "fulfillment-risk", True) is None
