"""D3: Unity Catalog for the workspace (needs a Databricks ACCOUNT admin, i.e. you after first account-console sign-in).

  1. ADLS Gen2 storage `stdbxucka2026` (HNS, shared key off) + container `unitycatalog`       (ARM)
  2. Databricks Access Connector `ac-skillagent-ka2026` (system identity) with
     Storage Blob Data Contributor on that storage account                          (ARM)
  3. Metastore for eastus (reuses an existing one in the account if present) and assign it to the workspace
  4. Storage credential (the access connector) + external location + catalog `skillagent`
     whose managed tables live in abfss://unitycatalog@stdbxucka2026.dfs.core.windows.net/skillagent

usage: python deploy/databricks/d3_metastore.py <databricks-account-id>      (rerun-safe)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from dbx import Dbx, workspace_url  # noqa: E402

SUB, RG, LOC, WS = "fe74279e-6ca8-48ff-bf54-90305b257322", "DB", "eastus", "dbw-skillagent-ka2026"
SA, CONTAINER, AC, CATALOG = "stdbxucka2026", "unitycatalog", "ac-skillagent-ka2026", "skillagent"
ARM = f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}/providers"
acct_id = sys.argv[1]
AZ = "az.cmd" if sys.platform == "win32" else "az"


def az(*a, check=True):
    r = subprocess.run([AZ, *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"az {' '.join(a[:3])}: {r.stderr[-600:]}")
    return r.stdout.strip()


def arm_put(path, body, ver):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
    try:
        return json.loads(az("rest", "--method", "put", "--url", f"{ARM}/{path}?api-version={ver}", "--body", f"@{f.name}") or "{}")
    finally:
        os.unlink(f.name)


def arm_get(path, ver):
    return json.loads(az("rest", "--method", "get", "--url", f"{ARM}/{path}?api-version={ver}") or "{}")


print("== 1. storage", SA)
arm_put(f"Microsoft.Storage/storageAccounts/{SA}", {
    "location": LOC, "kind": "StorageV2", "sku": {"name": "Standard_LRS"},
    "properties": {"isHnsEnabled": True, "allowSharedKeyAccess": False, "allowBlobPublicAccess": False,
                   "minimumTlsVersion": "TLS1_2", "supportsHttpsTrafficOnly": True}}, "2023-05-01")
for _ in range(30):
    if arm_get(f"Microsoft.Storage/storageAccounts/{SA}", "2023-05-01")["properties"].get("provisioningState") == "Succeeded":
        break
    time.sleep(10)
arm_put(f"Microsoft.Storage/storageAccounts/{SA}/blobServices/default/containers/{CONTAINER}", {"properties": {}}, "2023-05-01")
sa_id = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.Storage/storageAccounts/{SA}"

print("== 2. access connector", AC)
ac = arm_put(f"Microsoft.Databricks/accessConnectors/{AC}", {"location": LOC, "identity": {"type": "SystemAssigned"},
                                                             "properties": {}}, "2024-05-01")
for _ in range(30):
    ac = arm_get(f"Microsoft.Databricks/accessConnectors/{AC}", "2024-05-01")
    if ac.get("properties", {}).get("provisioningState") == "Succeeded":
        break
    time.sleep(10)
ac_id, ac_pid = ac["id"], ac["identity"]["principalId"]
az("role", "assignment", "create", "--assignee-object-id", ac_pid, "--assignee-principal-type", "ServicePrincipal",
   "--role", "Storage Blob Data Contributor", "--scope", sa_id, "-o", "none", check=False)
print("   connector identity", ac_pid, "-> Storage Blob Data Contributor on", SA)

print("== 3. metastore")
host = workspace_url(RG, WS, SUB)
ws_id = host.split("adb-")[1].split(".")[0]
acc = Dbx("https://accounts.azuredatabricks.net")
A = f"/api/2.0/accounts/{acct_id}"
ms = [m for m in acc.call("GET", f"{A}/metastores").get("metastores", []) if m.get("region") == LOC]
if ms:
    mid = ms[0]["metastore_id"]
    print("   reusing", ms[0]["name"], mid)
else:
    mid = acc.call("POST", f"{A}/metastores", {"metastore_info": {"name": f"metastore-{LOC}", "region": LOC}})[
        "metastore_info"]["metastore_id"]
    print("   created", mid)
cur = acc.call("GET", f"{A}/workspaces/{ws_id}/metastore", ok404=True) or {}
if (cur.get("metastore_assignment") or {}).get("metastore_id") != mid:
    acc.call("POST", f"{A}/workspaces/{ws_id}/metastores/{mid}",
             {"metastore_assignment": {"metastore_id": mid, "default_catalog_name": "main"}})
    print("   assigned to workspace", ws_id)

print("== 4. credential, external location, catalog")
d = Dbx(host)
for _ in range(20):
    if d.call("GET", "/api/2.1/unity-catalog/current-metastore-assignment", ok404=True):
        break
    time.sleep(10)
U = "/api/2.1/unity-catalog"
if not d.call("GET", f"{U}/storage-credentials/sc-skillagent", ok404=True):
    d.call("POST", f"{U}/storage-credentials", {"name": "sc-skillagent", "comment": "Access connector for skill agent data",
                                                "azure_managed_identity": {"access_connector_id": ac_id}})
url = f"abfss://{CONTAINER}@{SA}.dfs.core.windows.net/"
for i in range(12):  # the new role assignment can take a minute to apply
    try:
        if not d.call("GET", f"{U}/external-locations/el-skillagent", ok404=True):
            d.call("POST", f"{U}/external-locations", {"name": "el-skillagent", "url": url,
                                                       "credential_name": "sc-skillagent"})
        break
    except RuntimeError as e:
        print("   waiting for storage permission ...", str(e)[:120])
        time.sleep(20)
if not d.call("GET", f"{U}/catalogs/{CATALOG}", ok404=True):
    d.call("POST", f"{U}/catalogs", {"name": CATALOG, "storage_root": url + CATALOG,
                                     "comment": "Skill agent demo data"})
print("   catalogs:", [c["name"] for c in d.call("GET", f"{U}/catalogs").get("catalogs", [])])
print(f"METASTORE_ID={mid}\nCATALOG={CATALOG}\nD3_DONE")
