"""D2: serverless SQL warehouse (2X-Small, auto-stop 10 min) + Unity Catalog schema + synthetic orders.

usage: python deploy/databricks/d2_data.py   (needs `az login` as a workspace admin; rerun-safe: tables are replaced)
Prints the host, warehouse id and catalog for the next step. No secrets involved.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from dbx import Dbx, workspace_url  # noqa: E402
from gen_orders import generate  # noqa: E402

SUB, RG, WS = "fe74279e-6ca8-48ff-bf54-90305b257322", "DB", "dbw-skillagent-ka2026"
WAREHOUSE, SCHEMA = "skillagent-genie-wh", "sales"

host = workspace_url(RG, WS, SUB)
d = Dbx(host)
print("workspace:", host)

# --- Unity Catalog: new workspaces are auto-attached to the region's metastore with a workspace catalog
ma = d.call("GET", "/api/2.1/unity-catalog/current-metastore-assignment", ok404=True)
print("metastore:", (ma or {}).get("metastore_id"), "default catalog:", (ma or {}).get("default_catalog_name"))
cats = [c["name"] for c in d.call("GET", "/api/2.1/unity-catalog/catalogs").get("catalogs", [])]
print("catalogs:", cats)
catalog = os.environ.get("ORDERS_CATALOG") or (ma or {}).get("default_catalog_name") or \
    next((c for c in cats if c not in ("system", "samples", "hive_metastore", "main")), "main")

# --- SQL warehouse
whs = d.call("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
wh = next((w for w in whs if w["name"] == WAREHOUSE), None)
if wh is None:
    wh = d.call("POST", "/api/2.0/sql/warehouses", {
        "name": WAREHOUSE, "cluster_size": "2X-Small", "min_num_clusters": 1, "max_num_clusters": 1,
        "auto_stop_mins": 10, "enable_serverless_compute": True, "warehouse_type": "PRO",
        "spot_instance_policy": "COST_OPTIMIZED", "enable_photon": True})
    print("created warehouse", wh["id"])
wid = wh["id"]
for w in whs:  # the workspace's auto-created "Serverless Starter Warehouse" is left as-is but stopped
    if w["name"] != WAREHOUSE and w.get("state") in ("RUNNING", "STARTING"):
        d.call("POST", f"/api/2.0/sql/warehouses/{w['id']}/stop")
print("warehouse:", wid, "(2X-Small, serverless, auto-stop 10 min)")

# --- schema + tables
fq = f"`{catalog}`.`{SCHEMA}`"
d.sql(wid, f"CREATE SCHEMA IF NOT EXISTS {fq} COMMENT 'Synthetic order data for the skill agent demo (tied to Salesforce accounts)'")
ddl = {
    "accounts": """salesforce_account_id STRING NOT NULL COMMENT 'Salesforce Account Id (18 chars) - join key to Salesforce',
                   account_name STRING COMMENT 'Customer company name as in Salesforce', industry STRING, region STRING,
                   tier STRING COMMENT 'Enterprise or Mid-Market'""",
    "products": """product_id STRING NOT NULL, product_name STRING, category STRING COMMENT 'Hardware, Software, Services or Supplies',
                   list_price DECIMAL(12,2) COMMENT 'USD list price per unit'""",
    "orders": """order_id STRING NOT NULL, salesforce_account_id STRING NOT NULL COMMENT 'FK to accounts',
                 order_date DATE, ship_date DATE COMMENT 'NULL while Processing or if Cancelled',
                 status STRING COMMENT 'Processing, Shipped, Delivered, Cancelled or Returned',
                 channel STRING COMMENT 'Direct, Partner or Online', sales_rep STRING,
                 order_total DECIMAL(14,2) COMMENT 'Sum of order_lines.net_amount, USD', currency STRING""",
    "order_lines": """order_id STRING NOT NULL COMMENT 'FK to orders', line_number INT, product_id STRING COMMENT 'FK to products',
                      quantity INT, unit_price DECIMAL(12,2), discount_pct DECIMAL(5,2),
                      net_amount DECIMAL(14,2) COMMENT 'quantity * unit_price * (1 - discount_pct/100), USD'""",
}
comments = {
    "accounts": "Customer accounts; salesforce_account_id matches the Account Id in Salesforce",
    "products": "Product catalogue",
    "orders": "Sales orders (header) Jan 2025 onward; revenue = order_total excluding Cancelled/Returned",
    "order_lines": "Order line items",
}
accounts, products, orders, lines = generate()


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    if hasattr(v, "isoformat"):
        return f"DATE'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


for t, rows in (("accounts", accounts), ("products", products), ("orders", orders), ("order_lines", lines)):
    d.sql(wid, f"CREATE OR REPLACE TABLE {fq}.`{t}` ({ddl[t]}) COMMENT '{comments[t]}'")
    cols = list(rows[0].keys())
    for i in range(0, len(rows), 1000):
        vals = ",\n".join("(" + ",".join(lit(r[c]) for c in cols) + ")" for r in rows[i:i + 1000])
        d.sql(wid, f"INSERT INTO {fq}.`{t}` ({','.join(cols)}) VALUES {vals}")
    n = d.sql(wid, f"SELECT count(*) FROM {fq}.`{t}`")[0][0]
    print(f"  {catalog}.{SCHEMA}.{t}: {n} rows")

print(d.sql(wid, f"""SELECT a.account_name, count(*) orders, round(sum(o.order_total),2) revenue
                     FROM {fq}.orders o JOIN {fq}.accounts a USING (salesforce_account_id)
                     WHERE o.status NOT IN ('Cancelled','Returned') GROUP BY 1 ORDER BY 3 DESC"""))
print(f"DATABRICKS_HOST={host}\nWAREHOUSE_ID={wid}\nCATALOG={catalog}\nSCHEMA={SCHEMA}")
print("D2_DONE")
