"""Record the current Azure setup (read-only) so it can be recreated after teardown. Never writes secrets:
app-setting / named-value values whose names look secret are replaced with '<redacted>'.

usage: python ops/snapshot_azure.py <out_dir>        (run with `az login`, from the skill-agent repo root)
Writes <out_dir>/resources/*.json, appsettings/*.json, role_assignments.json, apim/*, genie_space.json, index.json
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

AZ = "az.cmd" if sys.platform == "win32" else "az"
SUB, RG = "fe74279e-6ca8-48ff-bf54-90305b257322", "DB"
OUT = sys.argv[1]
SECRETY = re.compile(r"(SECRET|PASSWORD|KEY|TOKEN|CONNECTION|INSTRUMENTATION|PAT|SAS|CREDENTIAL(?!$))", re.I)
OURS = {  # created by the skill-agent + fulfillment-risk projects (pre-existing resources are listed, not owned)
    "apim-skillagent-ka2026", "job-fr-refresh", "cae-fr-ka2026", "skillagentacrka2026", "ac-skillagent-ka2026",
    "dbw-skillagent-ka2026", "psql-fr-ka2026", "evhns-fr-ka2026", "appi-skillagent-ka2026", "fr-signals-rejected",
    "fr-signals-function-failures", "fr-signals-no-activity", "kv-skillagent-ka2026", "id-fr-signals-ka2026",
    "id-fr-refresh-ka2026", "log-skillagent-ka2026", "stfrsigka2026", "stdbxucka2026", "stskillagentka2026",
    "asp-skillagent-ka2026", "plan-fr-signals-ka2026", "skillagent-chat-ka2026", "func-fr-signals-ka2026",
    "Failure Anomalies - appi-skillagent-ka2026", "Application Insights Smart Detection"}


def az(*a, check=False):
    r = subprocess.run([AZ, *a, "-o", "json"], capture_output=True, text=True)
    if r.returncode and check:
        raise SystemExit(r.stderr[-500:])
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("value",) and isinstance(obj.get("name"), str) and SECRETY.search(obj["name"]):
                out[k] = "<redacted>"
            elif isinstance(v, str) and SECRETY.search(k) and k not in ("keyVaultReferenceIdentity",):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def save(rel, data):
    p = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(redact(data), f, indent=1, default=str)


res = az("resource", "list", "-g", RG, check=True)
dbx_mrg = az("resource", "list", "-g", "databricks-rg-dbw-skillagent-ka2026") or []
index = []
for r in res + dbx_mrg:
    full = az("resource", "show", "--ids", r["id"]) or r
    name = re.sub(r"[^A-Za-z0-9._-]", "_", r["name"])
    save(f"resources/{r['type'].split('/')[-1]}__{name}.json", full)
    index.append({"name": r["name"], "type": r["type"], "location": r["location"], "group": r["resourceGroup"],
                  "sku": (r.get("sku") or {}).get("name"), "ours": r["name"] in OURS or
                  r["resourceGroup"].startswith("databricks-rg-"), "id": r["id"]})
    print("recorded", r["type"], r["name"])

for app in ("skillagent-chat-ka2026", "func-fr-signals-ka2026"):
    save(f"appsettings/{app}.json", az("webapp", "config", "appsettings", "list", "-g", RG, "-n", app) or [])
save("appsettings/skillagent-chat-ka2026.authsettingsV2.json",
     az("rest", "--method", "get", "--url", f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}"
        "/providers/Microsoft.Web/sites/skillagent-chat-ka2026/config/authsettingsV2?api-version=2023-12-01"))
save("container_app_job.json", az("containerapp", "job", "show", "-g", RG, "-n", "job-fr-refresh"))
save("role_assignments.json", az("role", "assignment", "list", "-g", RG, "--include-inherited", "false"))
base = f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.ApiManagement/service/apim-skillagent-ka2026"
for rel, path in (("apim/named_values.json", "/namedValues"), ("apim/apis.json", "/apis"),
                  ("apim/api_policy.json", "/apis/skill-agent/policies/policy"),
                  ("apim/operation_policy.json", "/apis/skill-agent/operations/chat/policies/policy")):
    save(rel, az("rest", "--method", "get", "--url", base + path + "?api-version=2022-08-01"))
save("foundry_deployments.json", az("cognitiveservices", "account", "deployment", "list", "-g", RG, "-n",
                                    "db-accessHandOnsss3"))
save("keyvault_secret_names.json", [s["name"] for s in (az("keyvault", "secret", "list", "--vault-name",
                                                           "kv-skillagent-ka2026") or [])])
# Genie space definition (to recreate the same space)
try:
    tok = subprocess.run([AZ, "account", "get-access-token", "--resource", "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d",
                          "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True).stdout.strip()
    host = "https://adb-7405606799698174.14.azuredatabricks.net"
    req = urllib.request.Request(host + "/api/2.0/genie/spaces/01f1d15a0e621badafdfa059467efe9a?include_serialized_space=true",
                                 headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=60) as f:
        save("genie_space.json", json.loads(f.read()))
    req = urllib.request.Request(host + "/api/2.0/sql/warehouses/87f818da0e78701c", headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=60) as f:
        save("databricks_warehouse.json", json.loads(f.read()))
except Exception as e:
    print("genie/warehouse export skipped:", type(e).__name__)
save("index.json", index)
print(f"done: {len(index)} resources recorded in {OUT}")
