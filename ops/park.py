"""Park the demo to stop spending: delete the expensive pieces, pause the rest. Rebuild with ops/RECREATE.md.

Target (explicit): subscription fe74279e-6ca8-48ff-bf54-90305b257322, resource group DB.
Only resources these projects created are touched; pre-existing ones (kartikcosmosdb, deacoursekartik,
db-accessHandOnsss3 Foundry account + gpt-5, other resource groups) are never touched.

  1 pause   job-fr-refresh: schedule -> manual trigger (it would fail hourly without PostgreSQL)
  2 pause   func-fr-signals-ka2026: stopped (its Event Hub is deleted below)
  3 shrink  asp-skillagent-ka2026: B1 -> F1 (free). Web app, sign-in config and managed identity are kept
  4 DELETE  evhns-fr-ka2026 (Event Hubs Standard)
  5 DELETE  psql-fr-ka2026 (PostgreSQL Flexible B1ms; data is a recomputable snapshot + test signals)
  6 DELETE  dbw-skillagent-ka2026 (Databricks workspace; its managed RG incl. NAT gateway + public IP goes too.
            Unity Catalog metadata + table data stay in the metastore / stdbxucka2026)
usage: python ops/park.py --yes
"""
import json
import os
import subprocess
import sys
import tempfile
import time

AZ = "az.cmd" if sys.platform == "win32" else "az"
SUB, RG = "fe74279e-6ca8-48ff-bf54-90305b257322", "DB"
ARM = f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}/providers"


def az(*a, check=True):
    r = subprocess.run([AZ, *a, "--subscription", SUB, "-o", "json"], capture_output=True, text=True)
    if r.returncode and check:
        raise SystemExit(f"az {' '.join(a[:3])}: {r.stderr.strip()[-500:]}")
    return json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout.strip()


def step(m):
    print(f"\n[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def put(path, ver, body):
    fd, f = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as h:
        json.dump(body, h)
    try:
        return az("rest", "--method", "put", "--url", f"{ARM}/{path}?api-version={ver}", "--body", f"@{f}")
    finally:
        os.remove(f)


def exists(rid):
    r = subprocess.run([AZ, "resource", "show", "--ids", f"{ARM}/{rid}", "-o", "none"], capture_output=True, text=True)
    return r.returncode == 0


def main():
    if "--yes" not in sys.argv:
        sys.exit(__doc__)
    print(f"subscription {SUB} | resource group {RG}")

    step("1 job-fr-refresh: switch to manual trigger")
    job = az("rest", "--method", "get", "--url", f"{ARM}/Microsoft.App/jobs/job-fr-refresh?api-version=2024-03-01")
    cfg = job["properties"]["configuration"]
    if cfg.get("triggerType") != "Manual":
        cfg["triggerType"] = "Manual"
        cfg.pop("scheduleTriggerConfig", None)
        cfg["manualTriggerConfig"] = {"parallelism": 1, "replicaCompletionCount": 1}
        body = {"location": job["location"], "identity": job.get("identity"),
                "properties": {"environmentId": job["properties"]["environmentId"],
                               "workloadProfileName": job["properties"].get("workloadProfileName"),
                               "configuration": cfg, "template": job["properties"]["template"]}}
        put("Microsoft.App/jobs/job-fr-refresh", "2024-03-01", body)
    print("   trigger:", az("rest", "--method", "get", "--url",
                            f"{ARM}/Microsoft.App/jobs/job-fr-refresh?api-version=2024-03-01")["properties"]
          ["configuration"]["triggerType"])

    step("2 func-fr-signals-ka2026: stop")
    az("functionapp", "stop", "-g", RG, "-n", "func-fr-signals-ka2026", check=False)
    print("   state:", az("functionapp", "show", "-g", RG, "-n", "func-fr-signals-ka2026", "--query", "state",
                        check=False))

    step("3 asp-skillagent-ka2026: B1 -> F1 (free)")
    az("webapp", "config", "set", "-g", RG, "-n", "skillagent-chat-ka2026", "--always-on", "false", check=False)
    az("appservice", "plan", "update", "-g", RG, "-n", "asp-skillagent-ka2026", "--sku", "F1")
    print("   sku:", az("appservice", "plan", "show", "-g", RG, "-n", "asp-skillagent-ka2026", "--query", "sku.name"))

    for n, (label, rid) in enumerate((
            ("Event Hubs evhns-fr-ka2026", "Microsoft.EventHub/namespaces/evhns-fr-ka2026"),
            ("PostgreSQL psql-fr-ka2026", "Microsoft.DBforPostgreSQL/flexibleServers/psql-fr-ka2026"),
            ("Databricks dbw-skillagent-ka2026 (+ managed RG)", "Microsoft.Databricks/workspaces/dbw-skillagent-ka2026")),
            start=4):
        step(f"{n} DELETE {label}")
        if not exists(rid):
            print("   already gone")
            continue
        r = subprocess.run([AZ, "resource", "delete", "--ids", f"{ARM}/{rid}", "--subscription", SUB],
                           capture_output=True, text=True)
        print("   deleted" if r.returncode == 0 and not exists(rid) else f"   delete returned {r.returncode}: "
              f"{r.stderr.strip()[-300:]}")

    step("verify")
    left = az("resource", "list", "-g", RG, "--query", "[].{n:name, t:type, sku:sku.name}")
    for x in sorted(left, key=lambda x: x["t"]):
        print(f"   {x['t']:<55} {x['n']:<45} {x['sku'] or ''}")
    mrg = subprocess.run([AZ, "group", "exists", "-n", "databricks-rg-dbw-skillagent-ka2026", "--subscription", SUB],
                         capture_output=True, text=True).stdout.strip()
    print("   databricks managed RG still exists:", mrg)
    step("PARK_DONE")


if __name__ == "__main__":
    main()
