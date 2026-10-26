"""Read-only: Unity Catalog status of the workspace (metastore assignment, catalogs) and account-level metastores."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from dbx import Dbx, workspace_url  # noqa: E402

SUB, RG, WS = "fe74279e-6ca8-48ff-bf54-90305b257322", "DB", "dbw-skillagent-ka2026"
host = workspace_url(RG, WS, SUB)
d = Dbx(host)
for p in ("/api/2.1/unity-catalog/current-metastore-assignment", "/api/2.1/unity-catalog/metastore_summary",
          "/api/2.0/preview/scim/v2/Me"):
    try:
        r = d.call("GET", p)
        if p.endswith("/Me"):
            r = {"userName": r.get("userName"), "groups": [g.get("display") for g in r.get("groups", [])]}
        print(p, "->", r)
    except Exception as e:  # noqa: BLE001
        print(p, "->", str(e)[:300])
acct_id = os.environ.get("DATABRICKS_ACCOUNT_ID")
a = Dbx("https://accounts.azuredatabricks.net")
try:
    if not acct_id:
        print("set DATABRICKS_ACCOUNT_ID to probe the account API")
    else:
        ms = a.call("GET", f"/api/2.0/accounts/{acct_id}/metastores")
        print("account metastores:", [(m["name"], m.get("region"), m["metastore_id"]) for m in ms.get("metastores", [])])
except Exception as e:  # noqa: BLE001
    print("account API ->", str(e)[:400])
