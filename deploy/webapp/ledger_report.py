"""Print the token ledger: today's usage per user, this month's app total, and the latest calls.

usage: python deploy/webapp/ledger_report.py [--account stskillagentka2026] [--rows 10]
Needs Storage Table Data Reader on the account (step 10 grants it to you) and `az login`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from azure.data.tables import TableServiceClient
from azure.identity import AzureCliCredential

ap = argparse.ArgumentParser()
ap.add_argument("--account", default="stskillagentka2026")
ap.add_argument("--rows", type=int, default=10)
a = ap.parse_args()

svc = TableServiceClient(f"https://{a.account}.table.core.windows.net", credential=AzureCliCredential())
now = datetime.now(timezone.utc)
day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

usage = svc.get_table_client("tokenusage")
print(f"== counters ({day} / {month}, UTC)")
for e in usage.query_entities(f"RowKey eq '{day}' or RowKey eq '{month}'"):
    print(f"  {e['PartitionKey'][:48]:<48} {e['RowKey']:<10} {int(e['tokens']):>10,} tokens")

ledger = svc.get_table_client("tokenledger")
rows = sorted(ledger.query_entities(f"day eq '{day}'"), key=lambda e: e["RowKey"], reverse=True)[: a.rows]
print(f"\n== latest {len(rows)} ledger rows today")
for e in rows:
    print(f"  {e['RowKey'][:17]}  {e['status']:<28} {e.get('skill') or '-':<22} "
          f"in {int(e['input_tokens']):>6,}  out {int(e['output_tokens']):>5,}  total {int(e['total_tokens']):>6,}  "
          f"${float(e['cost_usd']):.4f}  user today {int(e['user_tokens_today']):>7,}")
