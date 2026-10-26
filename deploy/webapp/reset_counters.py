"""Reset token budget counters (tokenusage) to zero. The per-call ledger is kept; a `counter_reset` row is
added to it so the history shows who reset what and from which value.

usage:
  python deploy/webapp/reset_counters.py --user <entra-object-id>      # that user's counter for today (UTC)
  python deploy/webapp/reset_counters.py --user <oid> --app            # ... and the app's counter for this month
  python deploy/webapp/reset_counters.py --app                         # only the app's monthly counter
Needs Storage Table Data Contributor on tokenusage + tokenledger (or the account) and `az login`.
"""
from __future__ import annotations

import argparse
import getpass
from datetime import datetime, timezone

from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import AzureCliCredential

ap = argparse.ArgumentParser()
ap.add_argument("--account", default="stskillagentka2026")
ap.add_argument("--user", help="Entra object id of the user")
ap.add_argument("--app", action="store_true", help="also reset the app's monthly counter")
a = ap.parse_args()
if not (a.user or a.app):
    ap.error("give --user and/or --app")

svc = TableServiceClient(f"https://{a.account}.table.core.windows.net", credential=AzureCliCredential())
usage, ledger = svc.get_table_client("tokenusage"), svc.get_table_client("tokenledger")
now = datetime.now(timezone.utc)
targets = []
if a.user:
    targets.append((f"user-{a.user.lower()}", now.strftime("%Y-%m-%d")))
if a.app:
    targets.append(("app", now.strftime("%Y-%m")))

for pk, rk in targets:
    try:
        before = int(usage.get_entity(pk, rk).get("tokens", 0))
    except ResourceNotFoundError:
        before = 0
    usage.upsert_entity({"PartitionKey": pk, "RowKey": rk, "tokens": 0, "updated": now.isoformat()},
                        mode=UpdateMode.REPLACE)
    print(f"reset {pk}/{rk}: {before:,} -> 0")
    if a.user:
        ledger.create_entity({
            "PartitionKey": f"user-{a.user.lower()}",
            "RowKey": now.strftime("%Y%m%d%H%M%S%f")[:17] + f"-reset-{pk[:4]}",
            "day": now.strftime("%Y-%m-%d"), "status": "counter_reset", "skill": "", "session_id": "",
            "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
            "user_tokens_today": 0 if pk.startswith("user-") else -1,
            "app_tokens_month": 0 if pk == "app" else -1,
            "apim_request_id": f"manual reset of {pk}/{rk} from {before} by {getpass.getuser()}",
        })
