"""D4: Genie space over the orders tables + read-only access for the hosted agent's identity.

  1. Genie space (tables, instructions, join hints, sample questions)
  2. Add the agent identity (Entra service principal) to the workspace with Databricks SQL access only
  3. Grants: CAN_USE warehouse, CAN_RUN space, USE CATALOG/SCHEMA + SELECT on the orders schema
usage: python deploy/databricks/d4_genie.py <warehouse_id> [agent_principal_object_id]    (rerun-safe)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from dbx import Dbx, workspace_url  # noqa: E402

SUB, RG, WS = "fe74279e-6ca8-48ff-bf54-90305b257322", "DB", "dbw-skillagent-ka2026"
CATALOG, SCHEMA, TITLE = "skillagent", "sales", "Sales Orders (skill agent)"
AGENT_OID = sys.argv[2] if len(sys.argv) > 2 else "450a6723-dd2b-47a6-b1a5-e5f4227282d8"
wid = sys.argv[1]
AZ = "az.cmd" if sys.platform == "win32" else "az"
host = workspace_url(RG, WS, SUB)
d = Dbx(host)
fq = f"{CATALOG}.{SCHEMA}"
hid = lambda: uuid.uuid4().hex

INSTRUCTIONS = """Data is synthetic sales orders for three customer accounts that also exist in Salesforce.
- accounts.salesforce_account_id is the Salesforce Account Id; use it to join orders to accounts. Customer names: Acme Corp, Globex Inc, Initech.
- Revenue means SUM(orders.order_total) for orders whose status is NOT 'Cancelled' or 'Returned', unless the user asks otherwise. Amounts are USD.
- Line-level questions (products, quantities, discounts) use order_lines joined to orders on order_id and to products on product_id; product revenue = SUM(order_lines.net_amount).
- Order statuses: Processing, Shipped, Delivered, Cancelled, Returned. Channels: Direct, Partner, Online.
- Data runs from 2025-01-01 to 2026-10-25. "This year" means 2026; quarters are calendar quarters.
- Always include the account name (not only the id) in results grouped by customer."""
SAMPLES = ["What is total revenue by customer account in 2026?",
           "Show monthly order count and revenue for Acme Corp over the last 6 months",
           "Which 5 products generated the most revenue for Globex Inc?",
           "How many Initech orders are still Processing or Shipped?",
           "What is the average discount by channel?"]
EXAMPLE_SQL = (f"""SELECT a.account_name, ROUND(SUM(o.order_total), 2) AS revenue_usd
FROM {fq}.orders o JOIN {fq}.accounts a ON o.salesforce_account_id = a.salesforce_account_id
WHERE o.status NOT IN ('Cancelled', 'Returned') AND YEAR(o.order_date) = 2026
GROUP BY a.account_name ORDER BY revenue_usd DESC""")
TABLES = {"accounts": "Customer accounts; salesforce_account_id = Salesforce Account Id",
          "order_lines": "Order line items (quantity, unit price, discount, net amount)",
          "orders": "Sales order headers with status, channel, sales rep and order_total",
          "products": "Product catalogue with category and list price"}


def full_space():
    return {
        "version": 2,
        "config": {"sample_questions": [{"id": hid(), "question": [q]} for q in SAMPLES]},
        "data_sources": {"tables": [{"identifier": f"{fq}.{t}", "description": [TABLES[t]]} for t in sorted(TABLES)]},
        "instructions": {
            "text_instructions": [{"id": hid(), "content": [INSTRUCTIONS]}],
            "example_question_sqls": [{"id": hid(), "question": [SAMPLES[0]], "sql": [EXAMPLE_SQL]}],
        },
    }


def minimal_space():
    return {"version": 2, "data_sources": {"tables": [{"identifier": f"{fq}.{t}"} for t in sorted(TABLES)]}}


# ---- 1. Genie space
spaces = d.call("GET", "/api/2.0/genie/spaces").get("spaces", [])
space = next((s for s in spaces if s.get("title") == TITLE), None)
if space is None:
    body = {"warehouse_id": wid, "title": TITLE,
            "description": "Ask about orders, products and revenue for Acme Corp, Globex Inc and Initech."}
    try:
        space = d.call("POST", "/api/2.0/genie/spaces", {**body, "serialized_space": json.dumps(full_space())})
        print("created space with instructions + samples")
    except RuntimeError as e:
        print("full space rejected:", str(e)[:500])
        space = d.call("POST", "/api/2.0/genie/spaces", {**body, "serialized_space": json.dumps(minimal_space())})
        print("created minimal space (tables only)")
sid = space.get("space_id") or space.get("id")
got = d.call("GET", f"/api/2.0/genie/spaces/{sid}?include_serialized_space=true")
print("space:", sid, "| serialized keys:", list(json.loads(got.get("serialized_space") or "{}").keys()))

# ---- 2. agent identity -> Databricks service principal
gr = subprocess.run([AZ, "rest", "--method", "get", "--url",
                     f"https://graph.microsoft.com/beta/servicePrincipals/{AGENT_OID}?$select=appId,displayName"],
                    capture_output=True, text=True)
info = json.loads(gr.stdout or "{}")
app_id = info.get("appId")
print("agent identity:", info.get("displayName"), "appId", app_id)
if not app_id:
    sys.exit("could not resolve the agent identity's appId: " + gr.stderr[-300:])
sps = d.call("GET", f'/api/2.0/preview/scim/v2/ServicePrincipals?filter=applicationId%20eq%20%22{app_id}%22')
if sps.get("Resources"):
    sp = sps["Resources"][0]
else:
    sp = d.call("POST", "/api/2.0/preview/scim/v2/ServicePrincipals", {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"], "applicationId": app_id,
        "displayName": f"skill-agent ({info.get('displayName') or 'hosted agent'})",
        "entitlements": [{"value": "databricks-sql-access"}]})
print("databricks SP id", sp["id"])

# ---- 3. least-privilege grants
d.call("PATCH", f"/api/2.0/permissions/warehouses/{wid}",
       {"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_USE"}]})
d.call("PATCH", f"/api/2.0/permissions/genie/{sid}",
       {"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_RUN"}]})
for g in (f"GRANT USE CATALOG ON CATALOG {CATALOG} TO `{app_id}`",
          f"GRANT USE SCHEMA, SELECT ON SCHEMA {fq} TO `{app_id}`"):
    d.sql(wid, g)
print(d.sql(wid, f"SHOW GRANTS `{app_id}` ON SCHEMA {fq}"))
print(f"DATABRICKS_HOST={host}\nGENIE_SPACE_ID={sid}\nAGENT_APP_ID={app_id}\nD4_DONE")
