"""Fulfillment-risk screen: role gate and freshness banner logic (no database, no Streamlit server)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))
st = pytest.importorskip("streamlit")
import fr_dashboard as d  # noqa: E402

NOW = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)


def status(age_min=10, src_age_min=10, failures=0):
    t, s = NOW - timedelta(minutes=age_min), NOW - timedelta(minutes=src_age_min)
    return {"snapshot_at": t, "salesforce_as_of": s, "databricks_as_of": s, "snowflake_as_of": s,
            "failures_since_snapshot": failures}


def test_role_gate():
    assert not d.allowed(None) and not d.allowed({"roles": ["SkillAgent.User", "SkillAgent.Orders"]})
    assert d.allowed({"roles": ["SkillAgent.User", "SkillAgent.FulfillmentRisk"]})
    assert d.allowed({"roles": ["SkillAgent.Admin"]})


def test_freshness_levels():
    assert d.freshness_level(status(), NOW) == "ok"
    assert d.freshness_level(status(failures=2), NOW) == "failing"
    assert d.freshness_level(status(age_min=180, src_age_min=180), NOW) == "stale"      # hourly job missed
    assert d.freshness_level(status(src_age_min=25 * 60), NOW) == "stale"


def test_ago_and_money():
    assert d._ago(NOW - timedelta(minutes=5), NOW) == "5 min ago" and d._ago(NOW - timedelta(hours=5), NOW) == "5 h ago"
    assert d._money("20253.4") == "$20,253.40"
