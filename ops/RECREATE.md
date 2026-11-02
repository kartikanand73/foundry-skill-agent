# Parked on 2026-11-02: what was removed and how to bring it back

The demo was parked to stop spending (about $40 of credit left). The full configuration *before* parking is in
[`azure-snapshot-2026-11-02/`](azure-snapshot-2026-11-02/): every resource's settings, app settings, role
assignments, APIM policies and named values, the Genie space definition and the warehouse. Secret values are
redacted; secrets are kept in Key Vault, which was not touched.

## What `ops/park.py` did

| Resource | Action | Why |
|---|---|---|
| `dbw-skillagent-ka2026` (Databricks) | **deleted**, along with its managed resource group (NAT gateway, public IP, VNet, workspace storage) | NAT gateway and public IP bill around the clock |
| `psql-fr-ka2026` (PostgreSQL, centralus) | **deleted** | Fixed monthly charge; a stopped server restarts by itself after 7 days |
| `evhns-fr-ka2026` (Event Hubs Standard) | **deleted** | Fixed hourly charge |
| `asp-skillagent-ka2026` | **B1 → F1 (free)** | Keeps the web app, its Entra sign-in setup and its managed identity, which APIM and PostgreSQL trust |
| `job-fr-refresh` | **schedule → manual** | Its database is gone |
| `func-fr-signals-ka2026` | **stopped** | Its Event Hub is gone |

**Kept as is** (free or nearly free):
- Key Vault and its 4 secrets;
- Entra app registrations and app roles;
- the Foundry account, project, `gpt-5` deployment and the hosted agent (v11);
- APIM (Consumption tier);
- ACR (Basic, about $5 a month);
- storage accounts (threads and ledger, Unity Catalog data, Function host);
- App Insights and Log Analytics;
- managed identities;
- the Databricks access connector;
- the `fr-signals-*` alerts;
- the Container Apps environment.

**Never touched:** `kartikcosmosdb`, `deacoursekartik`, `kartikopenai` (resource group AI), the `db-prod` and
`MyMSFT` resource groups, and everything in Salesforce and Snowflake.

## Rebuild (about 45–60 minutes)

Run from your laptop with `az login` (the subscription owner).
- **`skill-agent` folder** (`C:\Users\karti\Downloads\skill-agent`): steps 1, 3 and the agent redeploy.
- **`fulfillment-risk-platform` folder:** steps 2 and 4.

### 1. Web app back to B1 (about 2 minutes)
```powershell
az appservice plan update -g DB -n asp-skillagent-ka2026 --sku B1
az webapp config set -g DB -n skillagent-chat-ka2026 --always-on true
```

### 2. PostgreSQL, refresh job and signals (about 20 minutes)
```powershell
cd C:\Users\karti\Downloads\fulfillment-risk-platform
.venv\Scripts\python.exe deploy\azure\provision.py --tag v2 --skip-build --only 4,5,7,8,9,10
.venv\Scripts\python.exe deploy\azure\signals.py up        # Event Hubs, roles, PG signal principal, Function, alerts
az functionapp start -g DB -n func-fr-signals-ka2026
.venv\Scripts\python.exe deploy\azure\signals.py smoke
```

What these do:
- **`provision.py`:**
  - recreates `psql-fr-ka2026` in centralus (same name, so no app setting changes);
  - applies the schema;
  - recreates the job's PostgreSQL principal (`fr_writer`) and the web app and agent principals (`fr_reader`, step 10);
  - puts the job back on its hourly schedule (step 8) and runs it once (step 9).
- **Step 9 will fail until Databricks is back** (step 3). That's expected: run step 9 again afterwards.

### 3. Databricks (about 15 minutes)
```powershell
cd C:\Users\karti\Downloads\skill-agent
powershell -File deploy\databricks\d1-workspace.ps1                               # new workspace: new URL / id
.venv\Scripts\python.exe deploy\databricks\d3_metastore.py 0a377d04-49dd-4826-8581-f95c29be1bae
.venv\Scripts\python.exe deploy\databricks\d2_data.py                              # new warehouse id; reloads tables
.venv\Scripts\python.exe deploy\databricks\d4_genie.py <new warehouse id>          # new Genie space id + agent grants
```

The workspace URL, warehouse ID and Genie space ID **change**. Update them in:
- **`skill-agent\.env`:** `DATABRICKS_HOST`, `GENIE_SPACE_ID`.
- **`fulfillment-risk-platform\.env`:** `DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID`.

The `skillagent` catalog, its storage credential and its external location live in the metastore, so `d3` finds them and skips creating them.

Then run:
```powershell
cd C:\Users\karti\Downloads\fulfillment-risk-platform
.venv\Scripts\python.exe deploy\databricks\open_orders_view.py
.venv\Scripts\python.exe deploy\azure\provision.py --tag v2 --skip-build --only 6,8,9   # job identity in Databricks, new settings, run
cd C:\Users\karti\Downloads\skill-agent
powershell -File deploy\hosted\redeploy.ps1 -Tag v12                                    # agent picks up the new Genie ids
```

### 4. Check everything
```powershell
.venv\Scripts\python.exe -m fulfillment_risk check-sources --live      # fulfillment-risk-platform folder
.venv\Scripts\python.exe deploy\azure\acceptance_signals.py            # expect 6/6
powershell -File deploy\webapp\13-test-fulfillment.ps1                 # skill-agent folder
powershell -File deploy\databricks\d5-test-agent.ps1
```
Then open the web app and check 📦 Fulfillment risk and the live signals section.

## Cost while parked
- **Ongoing:** ACR Basic (about $5 a month), plus small amounts for storage, Log Analytics ingestion and the 3 log alerts.
- **Pay per use, $0 when idle:** APIM Consumption, Flex Consumption (stopped), the Container Apps job (manual) and gpt-5.
- **Not ours:** check `kartikcosmosdb` separately; it may bill depending on its throughput mode.
