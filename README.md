# foundry-skill-agent-platform

End-to-end skill-routing agent on Microsoft Foundry Hosted Agents: a Streamlit chat app with threads (Entra sign-in, App Service) calls API Management, which calls the hosted agent. The agent routes each question to an HR, IT, Salesforce Agentforce or Snowflake Cortex skill. Every turn is traced in Application Insights and shows its model cost. See sections 3c-3e for the web front end, observability and cost per question.

At its core is a small, working Python agent that **picks an instruction package ("skill") per message, loads only that skill, exposes only that skill's tool, and runs real Python tools** against local synthetic JSON data. One core runtime is shared by a CLI and a Microsoft Foundry Hosted Agent entry point (with an Azure Container Apps fallback).

```
user message
   │
   ▼
① Router call ── model sees ONLY a catalog (name + one-line description per skill)
   │             and must return strict JSON: {skill: hr-policy|it-ticket|clarify|unsupported}
   ▼
② Validate choice against the catalog (anything else → unsupported)
   │
   ├─ clarify / unsupported → reply directly (no skill loaded, no tools)
   ▼
③ Load skills/<name>/SKILL.md from disk (fresh every request → hot reload)
   ▼
④ Skill call ── system = that skill's instructions; tools = ONLY its allowed_tools
   │   ↺ model requests tool → args validated → Python function reads JSON → result back
   │     (max 3 rounds, 30 s timeout per model call, then forced final answer)
   ▼
⑤ Final answer from the tool evidence
```

Conversation memory keeps only user/assistant **text** (last 6 turns). System prompts, skill instructions and tool definitions are rebuilt per request, so a skill used on one turn can't stay active on the next.

## Layout

```
agent/
  core.py       SkillAgent.handle(session_id, message) – the whole flow above
  skills.py     catalog (frontmatter only) + full skill loading
  tools.py      lookup_policy, get_ticket_status, ask_agentforce, schemas, validation, allowlist
  salesforce.py Agentforce Agent API client (OAuth, sessions, messages, Key Vault secret)
  llm.py        OpenAI SDK → Azure OpenAI v1 endpoint with Entra ID tokens
  config.py     env-based settings
  trace.py      [trace] lines on stderr (never credentials)
  fake_llm.py   OFFLINE scripted stand-in model for tests/demos only
skills/hr-policy/SKILL.md     skills/it-ticket/SKILL.md     skills/salesforce-agentforce/SKILL.md
data/policies.json            data/tickets.json
cli.py          local chat
main.py         Foundry Hosted Agent entry point (Invocations protocol, port 8088)
scripts/demo.py required demo cases + hot-reload demo
tests/          27 offline tests (incl. Agentforce API contract via mock transport)
Dockerfile  azure.yaml  deploy/aca/deploy.sh  .env.example
```

A skill package is plain markdown with frontmatter:

```yaml
---
name: it-ticket                  # must match the folder name
version: 1.0.0                   # printed in traces
description: Looks up the status of an existing IT support ticket ...   # the ONLY part the router sees
allowed_tools: [get_ticket_status]
---
...instructions...
```

## 1. Local setup

Requires Python 3.10+ and the Azure CLI.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env      # set AZURE_OPENAI_ENDPOINT and MODEL_DEPLOYMENT_NAME
```

**Model:** any deployment in your Foundry / Azure OpenAI resource that supports function calling and JSON-schema output (e.g. `gpt-4.1-mini`, `gpt-4o-mini`, `gpt-5.4-mini`). A mini model is plenty.

**Authentication (no keys):**

```bash
az login
az account show                       # confirm the subscription
```

Your user needs a data-plane role on the resource: **Foundry User** (formerly *Azure AI User*) or **Cognitive Services OpenAI User**. Being Owner/Contributor alone is not enough for data-plane calls.

```bash
az role assignment create --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Cognitive Services OpenAI User" \
  --scope "$(az cognitiveservices account show -n <account> -g <rg> --query id -o tsv)"
```

If you get a 401, set `AZURE_TOKEN_SCOPE=https://cognitiveservices.azure.com/.default`.

## 2. Run and test

```bash
python -m pytest -q                       # offline, no Azure needed
python scripts/demo.py                    # all demo cases against YOUR model
python cli.py                             # interactive chat (/reset, /quit)
python main.py                            # Foundry host locally on :8088
```

```bash
curl -X POST "localhost:8088/invocations?agent_session_id=s1" \
  -H "Content-Type: application/json" -d '{"message":"Check my ticket."}'
curl -X POST "localhost:8088/invocations?agent_session_id=s1" \
  -H "Content-Type: application/json" -d '{"message":"INC1002"}'
```

Add `SKILL_AGENT_FAKE_MODEL=1` to any command to run without Azure (scripted model).

### Hot reload

Skills are read from disk on every request. Edit `skills/it-ticket/SKILL.md` (bump `version`, add a rule) while `cli.py` or `main.py` is running; the next message prints `loaded_skill ... version=<new>` and follows the new rule. `scripts/demo.py` step 7 does this automatically on a temp copy.

In a container the skills are baked into the image, so a change there means a new image (Foundry creates a new agent version per `azd deploy`). To edit live in the cloud, mount an Azure Files share on Container Apps and point `SKILLS_DIR` at it.

## 3. Deploy to Foundry Hosted Agents

This is the sequence that was **executed successfully on 2026-10-20** against an existing Foundry project in East US with a `gpt-5` deployment. No local Docker was used.

Hosted Agents are GA. Python hosting uses `azure-ai-agentserver-invocations` (`InvocationAgentServerHost`, port 8088). azd uses the unified `azure.yaml` with `host: azure.ai.agent`, `kind: hosted`. A2A is not required.

**0. Tools and sign-in** (azd ≥ 1.27.1, extension `azure.ai.agents` ≥ 1.0.0-beta.8; tested with azd 1.36.0 / beta.21):

```bash
azd extension install azure.ai.agents azure.ai.projects
az login --tenant <tenant-id>              # the subscription's tenant, not your home tenant
azd auth login --tenant-id <tenant-id>
```

**1. Container registry and image** (cloud build, no Docker):

```bash
az acr create -g <rg> -n <acr> --sku Basic -l <region> --admin-enabled false
# let the Foundry project (and account) identities pull images
az role assignment create --assignee-object-id <project-principal-id> --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope $(az acr show -n <acr> --query id -o tsv)
az acr build -r <acr> -t skill-agent:v1 .
```

**2. azd environment.** `AGENT_IMAGE` and the `AZURE_*` values are azd variables, not container variables:

```bash
azd env new skill-agent-dev --subscription <sub-id> --location <region>
azd env set EXISTING_FOUNDRY_PROJECT_ENDPOINT https://<account>.services.ai.azure.com/api/projects/<project>
azd env set AZURE_AI_PROJECT_ID /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>
azd env set AZURE_AI_PROJECT_ENDPOINT https://<account>.services.ai.azure.com/api/projects/<project>
azd env set AZURE_RESOURCE_GROUP <rg>
azd env set MODEL_DEPLOYMENT_NAME gpt-5
azd env set MODEL_REASONING_EFFORT low
azd env set AGENT_IMAGE <acr>.azurecr.io/skill-agent:v1
azd env set AZD_AGENT_SKIP_ACR true
```

**3. Provision (no-op for an existing project) and deploy:**

```bash
azd provision --preview      # should list no new resources
azd provision
azd deploy                   # creates agent version, waits until active
azd ai agent show            # status, identity principal ID, invocations endpoint
```

**4. Give the agent identity access to the model.** azd did not assign this when reusing an existing project with a prebuilt image, so the first calls returned 401:

```bash
az role assignment create --assignee-object-id <Instance Identity Principal ID from show> \
  --assignee-principal-type ServicePrincipal --role "Foundry User" \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>
```

**5. Invoke.** Hosted sessions are created by the platform. azd creates one and reuses it until `--new-session`; you can't make up your own session IDs.

```bash
echo '{"message":"What is our vacation policy?"}' > p.json
azd ai agent invoke skill-agent -f p.json --protocol invocations --new-session
azd ai agent invoke skill-agent -f p2.json --protocol invocations      # same session → follow-up works
azd ai agent monitor skill-agent --session-id <id>                     # container logs incl. [trace] lines
```

### Gotchas found during deployment

| Symptom | Fix |
|---|---|
| `No container runtime (Docker/Podman) is installed` | Remove `language: docker`, add `docker: remoteBuild: true`, deploy a prebuilt `image:` |
| `Could not find ... infra\main.bicep` | Add `infra: provider: microsoft.foundry` (Bicep-less) |
| `AZURE_AI_PROJECT_ID is not set` | `azd env set AZURE_AI_PROJECT_ID <project ARM id>` |
| `Environment variable 'AGENT_TRACE' is reserved` | Platform reserves `AGENT_*` and `FOUNDRY_*`; this app uses `SKILL_AGENT_*` |
| `invalid_session_id` | Don't pass custom `--session-id`; let azd/platform create sessions |
| 401 from model inside container | Assign **Foundry User** on the account to the agent's instance identity |
| `azd auth token` says MFA required / wrong issuer | Log in to the subscription's tenant explicitly (`--tenant-id`) |
| `temperature` rejected | Reasoning models (gpt-5, o-series) don't accept it; leave `MODEL_TEMPERATURE` unset |

## 3a. Salesforce Agentforce skill

A third skill, `salesforce-agentforce`, sends CRM questions (accounts, contacts, opportunities, pipeline, customer cases) to an **existing Agentforce agent** through the [Agentforce Agent API](https://developer.salesforce.com/docs/einstein/genai/guide/agent-api-get-started.html). The pattern is unchanged: the router sees only the skill's description, and when it's selected the model is offered one tool, `ask_agentforce(question)`. That tool makes real HTTPS calls from the hosted agent to Salesforce:

```
POST https://<mydomain>.my.salesforce.com/services/oauth2/token     grant_type=client_credentials
POST https://api.salesforce.com/einstein/ai-agent/v1/agents/<agentId>/sessions      (once per conversation)
POST https://api.salesforce.com/einstein/ai-agent/v1/sessions/<sessionId>/messages  {sequenceId, type: Text, text}
DELETE .../sessions/<sessionId>   x-session-end-reason: UserRequest                 (on /reset or idle expiry)
```

- **One Agentforce session per conversation.** Follow-ups keep context on the Salesforce side, and `sequenceId` increments for each message. If Salesforce expires a session (404/410), it's recreated once automatically.
- **Tokens** are cached for 25 minutes and refreshed on a 401. Tokens, secrets and full session IDs are never traced.
- **Failures** (auth, timeout, HTTP errors) come back to the model as structured errors. The skill tells it to report that Salesforce is unavailable rather than guess.
- **Not configured?** The skill still routes, but answers "Salesforce is not configured", so the HR and IT skills are unaffected.

### Salesforce side (you create this)
1. **External Client App** with OAuth scopes `api`, `refresh_token, offline_access`, `chatbot_api`, `sfap_api`. Enable **Client Credentials Flow**, enable JWT-based access tokens, and set **Run As** to a user with API access.
2. An **Agentforce agent** (not the "Agentforce (Default)" type, which the API doesn't support). Activate it, connect the External Client App to it, and note its ID (`0Xx…`).

### Azure side (when you deploy)
```bash
# store the consumer secret in Key Vault (never in azure.yaml, .env in git, or the image)
az keyvault secret set --vault-name <vault> -n salesforce-client-secret --value '<consumer secret>'
# let the hosted agent's identity read it (principal = "Instance Identity Principal ID" from `azd ai agent show`)
az role assignment create --assignee-object-id <agent-principal-id> --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" --scope $(az keyvault show -n <vault> --query id -o tsv)

azd env set SALESFORCE_MY_DOMAIN https://<mydomain>.my.salesforce.com
azd env set SALESFORCE_CLIENT_ID <consumer key>
azd env set SALESFORCE_AGENTFORCE_ID 0Xx...
azd env set SALESFORCE_KEYVAULT_URL https://<vault>.vault.azure.net/
```
Then rebuild the image (`az acr build`), update `AGENT_IMAGE` to the new tag, and run `azd deploy`. Hosted agents allow outbound internet by default; if the project uses `AllowOnlyApprovedOutbound`, allow `*.my.salesforce.com` and `api.salesforce.com`.

**Locally**, put the same values in `.env`. Either use `SALESFORCE_KEYVAULT_URL` (read with your `az`/`azd` login) or, for quick tests only, `SALESFORCE_CLIENT_SECRET`.

### Verified against a live org (2026-10-23)
A Developer Edition org with an Employee Agent, tested from the laptop through `gpt-5`: the token, session start, message, reply and session end all succeeded.
- **Employee agents need `SALESFORCE_BYPASS_USER=false`.** With `true`, session start fails with `400 Invalid user ID provided on start session`, because employee agents have no dedicated agent user and run as the Run As user.
- Agentforce answers only from the subagents and actions it has. A default employee agent with General FAQ, Off Topic and Ambiguous Question can't read records until you add a subagent with record actions (e.g. Query Records / Get Record Details), and the Run As user needs access to those objects.

**Deployed and verified hosted (2026-10-23):** agent version 2, image `skill-agent:v2`. The secret is read from Key Vault with the agent's managed identity, so the container has no secret in its environment. A Salesforce account list and a follow-up in the same session, an HR question and an IT question all passed.

**Rotating the secret:** reset it in the External Client App, then run `az keyvault secret set --vault-name <kv> -n salesforce-client-secret --file <file>`. The hosted agent picks up the new value at its next token refresh (within 25 minutes), with no redeploy.

### Try it
```json
{"message": "What open opportunities does the Acme account have in Salesforce?"}
{"message": "Who is the primary contact on that account?"}
```
The second message reuses the same Agentforce session, so "that account" is resolved on the Salesforce side.

## 3b. Snowflake Cortex Agent skill

`snowflake-cortex` sends analytics questions to an **existing Cortex Agent object** through the [Cortex Agents Run API](https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-agents-run). The agent's semantic view stays the single definition of each metric.

```
POST https://<account>.snowflakecomputing.com/api/v2/databases/<db>/schemas/<schema>/agents/<name>:run
Authorization: Bearer <PAT>        X-Snowflake-Authorization-Token-Type: PROGRAMMATIC_ACCESS_TOKEN
{"messages": [...last turns..., {"role":"user","content":[{"type":"text","text":"..."}]}], "stream": false}
```

- The tool returns the agent's `answer` plus the `sql` it ran and its result `tables` (capped at 25 rows), so `gpt-5` answers from the actual rows and can show the SQL when asked.
- **Follow-ups:** the last 4 turns of the conversation are resent in `messages`, so no server-side threads are needed.
- **Auth:** a programmatic access token (PAT) for a user whose default role can use the agent, its semantic view and a warehouse. Hosted, the PAT is read from Key Vault (`snowflake-pat`); locally, from `SNOWFLAKE_PAT`. On a 401 the token is re-read, so rotating it in Key Vault needs no redeploy.
- **Network policy:** PATs normally require the user to be covered by a Snowflake network policy. The hosted agent's outbound IPs aren't fixed, so use an authentication policy that allows PATs without a network policy for this service user, or allow-list the egress range.

## 3c. Web front end: Streamlit + API Management

```
Browser --Entra sign-in--> Streamlit (App Service B1) --subscription key--> APIM (Consumption)
        --APIM managed identity (Foundry User)--> Foundry hosted agent (invocations)
```

- **`webapp/`**: Streamlit chat UI. The left sidebar lists threads ("+ New chat", rename, delete). **Each thread is one hosted-agent session**: the first message gets a session ID back from Foundry (`x-agent-session-id`), and follow-ups in that thread send it again, so each thread keeps its own context. Threads live in the browser session.
- **`infra/apim-policy.xml`**: rate limit of 30/min, removes the caller's key, gets an Entra token for `https://ai.azure.com` with APIM's managed identity, and rewrites `/chat` to `/invocations?api-version=v1` (keeping `agent_session_id`).
- **Secrets:** the APIM subscription key is in Key Vault, and the web app reads it through a Key Vault reference with its managed identity. APIM calls Foundry with no key at all.
- **Deploy (PowerShell, from the repo root):** `deploy/webapp/0-verify-session.ps1` → `1-apim.ps1` → `2-test-apim.ps1` → `3-webapp.ps1` → `4-entra-auth.ps1`.
- **Lock APIM to the web app (step 7):** `deploy/webapp/7-apim-jwt.ps1`. APIM's `validate-jwt` requires an Entra token issued to the **web app's managed identity**: audience `api://<skillagent-api-ka2026 app id>`, `oid` = the web app's principal ID. That's on top of the subscription key, so a leaked key alone gets **401**. The web app gets the token with `ManagedIdentityCredential` (`APIM_TOKEN_AUDIENCE`). `2c-test-apim-locked.ps1` checks that the no-key, key-only and user-token calls are all refused.
  - Verified 2026-10-26: key only gave 401 (it was 200 before); a chat through the web app answered normally.
- **Redeploy code only:** `deploy/webapp/3b-deploy-code.ps1`.
- **Deploy gotchas (hit and fixed):**
  - Don't zip with `Compress-Archive`. Its entries have no Unix permissions, so Kudu's rsync fails (exit 23, shown as "Kudu Status 400"). `make_zip.py` builds the zip instead.
  - `az.cmd` mangles the `( ) ;` in Key Vault references, so app settings go in through a JSON file.
  - A new app is still on the auth v1 format, and `az webapp auth` (v2) refuses it. Step 4 PUTs `authsettingsV2` through ARM, then restarts the app (`az webapp restart`) before sign-in is enforced.
  - The redirect-to-sign-in only starts after that restart.
- **Verified 2026-10-25 on the live site:**
  - an HR question answered by the hr-policy skill;
  - a follow-up in the same thread kept its context;
  - "New chat" then an IT ticket question, answered by the it-ticket skill;
  - switching back to the first thread restored its history;
  - after step 4, signed-out visitors are redirected to login.microsoftonline.com.
  - after signing in, the Snowflake question "total paid claims in Q1 2026" returned $370,685,724.65, served by hosted agent v3 (image `skill-agent:v3`). Through APIM, the follow-up about the denial rate returned 45.08%, measured in dollars.
- **Redeploy the hosted agent:** `deploy/hosted/redeploy.ps1 -Tag vN` runs the ACR cloud build, sets the non-secret Snowflake settings in the azd environment, and runs `azd deploy`. The Snowflake PAT stays in Key Vault as `snowflake-pat`.
- **Run locally:** `cd webapp; pip install -r requirements.txt; $env:APIM_BASE_URL="https://<apim>.azure-api.net/skill-agent"; $env:APIM_SUBSCRIPTION_KEY="<key>"; streamlit run app.py`
- **Delete:** `az webapp delete -n <app> -g <rg>; az appservice plan delete -n <plan> -g <rg> --yes; az apim delete -n <apim> -g <rg> --yes; az ad app delete --id <auth app id>`

## 3d. Observability: Application Insights (end-to-end traces)

`deploy/webapp/5-app-insights.ps1` creates one workspace-based Application Insights resource (`appi-skillagent-ka2026`, with Log Analytics `log-skillagent-ka2026`) and wires every hop to it. After running it, redeploy the hosted agent (`deploy/hosted/redeploy.ps1 -Tag vN`) and the web app (`3b-deploy-code.ps1`).

| Hop | How | Shows up as (`cloud_RoleName`) |
|---|---|---|
| Streamlit web app | `azure-monitor-opentelemetry` plus the httpx instrumentor: a `webapp.chat_turn` span, and `traceparent` sent to APIM | `skillagent-webapp` |
| APIM | App Insights logger plus API diagnostics: W3C correlation, 100% sampling, **no bodies** | `apim-skillagent-ka2026 East US` |
| Foundry platform | Project monitoring: the Foundry project has an App Insights connection | `agentsv2` |
| Hosted agent | Connection string injected by Foundry; the runtime's OpenTelemetry distro exports the agent's spans | `skill-agent` |

**Agent spans:**
- `skill_agent.turn` records skill, version, tools, tool status and outcome.
- `skill_agent.route` records the router's choice.
- `skill_agent.tool` records tool, status and error.
- Each trace line becomes a span event.
- httpx dependency spans cover the model, Salesforce and Snowflake calls.

**Prompt and answer text are not recorded.** Set `SKILL_AGENT_TRACE_CONTENT=1` to include them.

**Check it:** run `deploy/webapp/6-verify-traces.ps1`, or use this KQL:

```kusto
union AppRequests, AppDependencies
| where TimeGenerated > ago(1h)
| summarize components=make_set(AppRoleName), spans=count() by OperationId
```

In the portal: App Insights → **Transaction search**, then open a turn to see the end-to-end view.

**Verified 2026-10-25:** an APIM test turn was one OperationId spanning APIM, `agentsv2` and `skill-agent`, with `skill_agent.turn` and `skill_agent.tool` (it-ticket / get_ticket_status / found) inside it. Cost is pay-as-you-go ingestion, which is pennies for a demo. To remove it, delete the two resources and the project connection.

## 3e. What did that question cost?

Every model response reports exact token counts. The agent adds them up for one question (router call plus skill calls) and prices them.

- **API response:** a `usage` object with `model_calls`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens` and `cost_usd`.
- **Web app:** shows `💲 $0.0xxx model cost · N in / M out tokens` under each answer, the thread total under the title, and the session total in the sidebar.
- **Application Insights:** the same numbers are attributes on the `skill_agent.turn` span:

```kusto
AppDependencies
| where Name == "skill_agent.turn"
| extend skill=tostring(Properties["skill_agent.skill"]),
         cost=todouble(Properties["skill_agent.cost_usd"]),
         tin=toint(Properties["skill_agent.input_tokens"]), tout=toint(Properties["skill_agent.output_tokens"])
| summarize questions=count(), cost_usd=sum(cost), avg_cost=avg(cost), input_tokens=sum(tin), output_tokens=sum(tout)
    by skill, day=bin(TimeGenerated, 1d)
| order by day desc, cost_usd desc
```

**Rates:** the defaults are gpt-5 Global Standard list prices, in USD per 1M tokens: input $1.25, cached input $0.125, output $10. Reasoning tokens are billed as output. Override them on the agent with `MODEL_PRICE_INPUT_PER_1M`, `MODEL_PRICE_CACHED_INPUT_PER_1M` and `MODEL_PRICE_OUTPUT_PER_1M`.

**This is the model cost only.** It's an estimate from list prices; your Azure invoice is authoritative. It doesn't include:
- Snowflake credits (on your Snowflake bill)
- hosted-agent compute (~$0.03/hour while active at 0.25 vCPU / 0.5 GiB)
- APIM, App Service, ACR or App Insights

## 3f. Token budgets and ledger (APIM)

Every call through APIM is checked against token budgets and recorded. Tokens are the agent's own count (router plus skill calls) from the response's `usage`.

| Piece | Where | What |
|---|---|---|
| Budget check (inbound) | `infra/apim-policy.xml` (operation) | Reads `tokenusage(user-<oid>, <yyyy-MM-dd>)` and `tokenusage(app, <yyyy-MM>)`. If either is at its limit: **429**, plus a `rejected_*` ledger row. |
| Charging (outbound) | `infra/apim-api-policy.xml` (API level) | One `tokenledger` row per answered call (status `ok`, `blocked` or `forbidden`; user, skill, session, in/out/total tokens, cost, running totals). Adds the call's tokens to both counters: read, then write. Two simultaneous calls from one user can under-count the counter; the ledger stays exact. |
| Limits | APIM named values | `token-limit-user-daily` = 50,000; `token-limit-app-monthly` = 2,000,000 |
| Storage | `stskillagentka2026` tables `tokenledger`, `tokenusage` | APIM's managed identity has Storage Table Data Contributor on those two tables only |
| Client view | Response headers `x-tokens-*`, `x-token-ledger` | The web app shows "Tokens today: N of 50,000" in the sidebar |

Notes:

- **The policies are generated.** Edit `deploy/webapp/gen_policy.py`, then run `python deploy/webapp/gen_policy.py infra/apim-policy.xml infra/apim-api-policy.xml`. The split exists because Consumption caps each policy document at 16 KiB.
- **Limits are soft.** Budgets are checked before a call and charged after it, so a single call can finish a little over a limit.
- **The ledger fails open.** If the ledger can't be read, calls are allowed and flagged `x-token-ledger: degraded`.
- **Change a limit:** `az apim nv update -n apim-skillagent-ka2026 -g DB --named-value-id token-limit-user-daily --value 75000`
- **Report:** `python deploy/webapp/ledger_report.py`

## 3g. Guardrails: Foundry + code

| Layer | What | Where |
|---|---|---|
| **Foundry guardrail** `skill-agent-guardrail` on the `gpt-5` deployment | Hate, sexual, violence and self-harm blocked at **Medium** on input and output; **jailbreak** and **indirect attack** blocked on input; protected material: text blocked, code annotated | `deploy/webapp/11-guardrail.ps1`. Portal: Foundry → Guardrails, or Models + endpoints → gpt-5 |
| **Authorization** (code) | Every user needs `SkillAgent.User`. Each skill declares `required_role` in its SKILL.md; `SkillAgent.Admin` can use every skill. The check runs **before the skill loads**, and a refused request never reaches a tool. Hosted: `SKILL_AGENT_REQUIRE_ROLES=true`, so anonymous calls are refused. | `agent/guardrails.py`, `skills/*/SKILL.md` |
| **Guardrail block handling** (code) | A blocked prompt (`content_filter` 400) or answer (`finish_reason=content_filter`) becomes a clear "blocked by the content safety policy (categories)" reply, with a `guardrail_blocked` trace event and span attributes | `agent/core.py` `_chat()` |
| **Spotlighting** (code) | Tool results are wrapped in `<tool_result>` tags, and the system prompt says their content is data, never instructions | `agent/guardrails.py` |
| Already in place | Router limited to the skill list, tool allowlist enforced in code, argument validation, max 3 tool rounds, read-only tools, 4,000-character input cap, sanitized errors; APIM identity check, rate limit and token budgets | see above |

Why the model deployment: the hosted agent calls `gpt-5` directly, so the deployment's guardrail screens every router and skill call, including tool results sent back to the model. Foundry's agent-level tool call / tool response checks (preview) only apply to Foundry-managed tools, not Python tools inside a hosted container.

Revert the model guardrail: run `11-guardrail.ps1 -Policy Microsoft.DefaultV2`. The script then only re-assigns the deployment, because a system policy can't be overwritten. Use the portal if it refuses.

## 3h. Testing and inspecting the live system

| Script (in `deploy/webapp/`) | What it checks |
|---|---|
| `inspect-config.ps1` | Read-only snapshot of every resource's live settings: web app, Entra roles and assignments, APIM (policies, named values, logging), Foundry guardrail, storage, Key Vault, identities. Never prints secret values. |
| `2c-test-apim-locked.ps1` | APIM refuses calls with no key, with the key only, and with a non-web-app token (all 401) |
| `2d-test-foundry-direct.ps1` | The Foundry agent endpoint refuses calls with no token (401); a Foundry token with no signed-in user gets "Please sign in"; **known gap:** a direct caller asserting a made-up user is answered |
| `ledger_report.py` | Today's token counters and the latest ledger rows |
| `recent-calls.ps1 -Minutes N` | Requests, dependencies and exceptions across all components (App Insights) |
| `6-verify-traces.ps1` | End-to-end traces and agent spans |

**Results from 2026-10-26:** 74 tests; 47 pass, 0 failing, 8 known gaps, 19 not run (they need a second user, a re-sign-in with fewer roles, or a dependency taken down).

**Defect found and fixed:** the first update of an existing token counter hung APIM for 150 s. The ETag + retry update was replaced with read-then-write.

**Known gaps:**

1. **Direct calls can skip APIM.** Anyone with **Foundry User** on the Foundry account can call the agent directly with a made-up `user` (identity and budget bypass). Fix: limit that role to APIM's identity, or have APIM sign the user context and the agent verify it.
2. **Exposed secrets.** The Snowflake PAT and Salesforce secret were shared in chat; rotate both.
3. **Any tenant user can sign in.** "Assignment required" is off on `skillagent-chat-ka2026-auth`.
4. **Broad subscription access.** 37 Owner/Contributor assignments exist at subscription level.
5. **Key Vault scope.** The agent's identity can read all three Key Vault secrets.
6. **Counter race.** Simultaneous calls can under-count the counter; the ledger stays exact.

**Identities** (object IDs, for reading role assignments):

| Identity | Object ID | Role listings show |
|---|---|---|
| Web app | `4db65470-…` | application ID `cbc9e8bb-…` |
| APIM | `52aa6f83-…` | application ID `226532e6-…` |
| Hosted agent | `450a6723-…` | |
| Sign-in app registration | `skillagent-chat-ka2026-auth` | |
| API audience app registration | `skillagent-api-ka2026` (`api://d4866a79-…`) | |

**Where to look in APIM:**

- **Security → Managed identities:** APIM's identity and its role assignments.
- **APIs → skill-agent:**
  - **Settings:** subscription required.
  - **Design → POST /chat → Policies:** identity check, rate limit, budget check, routing.
  - **Design → All operations → Policies:** charging.
  - **Settings → Diagnostics:** App Insights logging, 100% sampling, W3C, no bodies.
- **Named values:** limits, audience, allowed web-app identity.
- **Subscriptions:** `streamlit-app` key. If you regenerate it, also update Key Vault `apim-subscription-key`.

## 3i. Databricks Genie skill (sales orders)

`databricks-genie` answers order questions by asking an **AI/BI Genie space** through the [Genie Conversation API](https://learn.microsoft.com/azure/databricks/genie/conversation-api). The skill needs the `SkillAgent.Orders` role.

```
POST {host}/api/2.0/genie/spaces/{space}/start-conversation          {"content": "..."}    first question
POST {host}/api/2.0/genie/spaces/{space}/conversations/{cid}/messages {"content": "..."}    follow-up (same session)
GET  .../conversations/{cid}/messages/{mid}                          poll until COMPLETED / FAILED
GET  .../messages/{mid}/query-result/{attachment_id}                  rows of the SQL Genie ran (capped at 25 here)
```

- **No secret.** The hosted agent's own Entra identity (`450a6723-…`, the AgentIdentity) gets a token for the AzureDatabricks resource. It is a Databricks service principal with only:
  - Databricks SQL access;
  - CAN_USE on the warehouse;
  - CAN_RUN on the space;
  - USE CATALOG `skillagent`;
  - USE SCHEMA + SELECT on `skillagent.sales`.
- **Follow-ups:** one Genie conversation per agent session, so "and for Initech only?" keeps its context.
- **Data:** synthetic orders, generated deterministically by `deploy/databricks/gen_orders.py`. Every order carries the **Salesforce Account Id**, so Genie and the Salesforce skill talk about the same customers.

| Table (`skillagent.sales`) | Rows | Notes |
|---|---|---|
| `accounts` | 3 | Acme Corp `001fj000026lzjWAAQ`, Globex Inc `001fj000026l3pEAAQ`, Initech `001fj000026m1nLAAQ` |
| `products` | 20 | Hardware, Software, Services, Supplies |
| `orders` | 2,000 | 2025-01-01 → 2026-10-25; status, channel, sales rep, `order_total` |
| `order_lines` | 5,975 | quantity, unit price, discount, `net_amount` |

**Setup scripts** (`deploy/databricks/`, run as a workspace and account admin, all rerun-safe):

| Step | What it does |
|---|---|
| `d1-workspace.ps1` | Creates the Premium workspace `dbw-skillagent-ka2026` in RG `DB` (eastus). There is no fixed fee; you pay only for compute while it runs. |
| `d3_metastore.py <account-id>` | Unity Catalog setup: ADLS Gen2 `stdbxucka2026` (shared key off), access connector `ac-skillagent-ka2026`, assigns the existing eastus metastore, then creates a storage credential, external location and catalog `skillagent`. |
| `d2_data.py` | Creates the serverless SQL warehouse `skillagent-genie-wh` (2X-Small, auto-stop 10 min) and loads the four tables. |
| `d4_genie.py <warehouse-id>` | Creates the Genie space (instructions, example SQL, sample questions), adds the agent identity as a service principal and applies the grants. |
| `ask_genie.py "question" ...` | Asks the space as you, using the same client the agent uses. |

**Settings:** `DATABRICKS_HOST` and `GENIE_SPACE_ID` live in `.env` and are passed to the hosted agent by `redeploy.ps1`. Neither is secret.

**Cost:** the warehouse bills serverless SQL DBUs only while it runs. A 2X-Small uses about 4 DBU per hour, and the warehouse stops after 10 idle minutes. Check the current per-DBU rate on the [Azure Databricks pricing page](https://azure.microsoft.com/pricing/details/databricks/). The workspace and storage cost almost nothing when idle.

## 4. Fallback: Azure Container Apps

Same image, same `main.py`, scale-to-zero:

```bash
export SUBSCRIPTION_ID=... LOCATION=eastus2 RG=rg-skill-agent \
       AOAI_RESOURCE_ID=$(az cognitiveservices account show -n <account> -g <rg> --query id -o tsv) \
       AOAI_ENDPOINT=https://<account>.services.ai.azure.com MODEL_DEPLOYMENT_NAME=gpt-4.1-mini
bash deploy/aca/deploy.sh      # confirms the subscription before creating anything
```

Creates: resource group, ACR (Basic), Container Apps environment, container app with a system-assigned identity granted **Cognitive Services OpenAI User** on your model resource. Ingress is public and unauthenticated by default. Fine for a demo, not beyond that.

## 5. Resources and cost

What the deployment created in your subscription (everything else was reused):

| Resource | Where | Cost |
|---|---|---|
| Container registry `<acr>` (Basic) | your resource group | roughly $5/month while it exists |
| Hosted agent `skill-agent` (version 1), 0.25 vCPU / 0.5 GiB | your Foundry project | vCPU + memory hours only while a session's compute is running; idle sessions don't use compute |
| Role assignments: AcrPull (project + account identities), Foundry User (agent identity) | ACR / account scope | free |
| Key Vault `<kv>` (Standard, RBAC) holding `salesforce-client-secret`; agent identity = Key Vault Secrets User | your resource group | a few cents per 10k operations |

Model usage is billed on your existing `gpt-5` deployment. Each message is 1 call (unsupported/clarify) or 3 calls (route → tool request → answer). `gpt-5` costs more per token than a mini model; for a cheaper demo, deploy `gpt-5-mini` (or similar) and set `MODEL_DEPLOYMENT_NAME`. Check the Azure pricing calculator for current rates.

Container Apps fallback (not used): RG + ACR Basic + consumption environment that scales to zero.

## 6. Delete

```bash
# 1. the hosted agent (all versions)
azd ai agent delete skill-agent
# 2. the Key Vault (soft-delete keeps it 90 days unless purged)
az keyvault delete -n <kv> -g <rg> && az keyvault purge -n <kv>
# 3. the container registry
az acr delete -n <acr> -g <rg> --yes
# 4. optional: the agent's role assignment on the account (gone with the agent identity anyway)
```

`azd down` is **not** needed here and would not delete your existing project. Your Foundry account, project, `gpt-5` deployment and resource group `DB` are left untouched by both commands.

Also created later; delete these too:

- **App registrations:** `skillagent-chat-ka2026-auth` and `skillagent-api-ka2026`.
- **Storage account:** `stskillagentka2026`.
- **Monitoring:** Application Insights `appi-skillagent-ka2026` and Log Analytics `log-skillagent-ka2026`.
- **Foundry guardrail:** first point the gpt-5 deployment back to `Microsoft.DefaultV2`, then delete `skill-agent-guardrail` (Foundry → Guardrails).
- **Foundry project connection:** `appi-skillagent-ka2026`.
- **Databricks:**
  - Workspace `dbw-skillagent-ka2026`; deleting it also removes its managed RG `databricks-rg-dbw-skillagent-ka2026`.
  - Access connector `ac-skillagent-ka2026` and storage `stdbxucka2026`.
  - In the account console: catalog `skillagent`, storage credential `sc-skillagent` and external location `el-skillagent`. The shared eastus metastore stays.


## 7. What was verified (2026-10-20)

| Item | Status |
|---|---|
| 16 offline tests: tools, validation, allowlist, catalog-only routing, no cross-skill leakage, bounded loop, error handling, bounded history, hot reload | Verified |
| All demo cases against **your real `gpt-5`** locally (vacation, INC1001, missing ID + follow-up, unknown ticket, unrelated, both-skills clarify, hot reload v1.1.0) | Verified |
| Image built in Azure (`az acr build`), pushed to `<acr>` | Verified |
| `azd provision` (existing project, no new resources) + `azd deploy` → `skill-agent` v1 **active** | Verified |
| Hosted invocations: vacation, INC1001, "Check my ticket" → follow-up in same session, INC9999, unrelated | Verified (all pass after role propagation) |
| Local Docker build | Not run (no Docker on the laptop; not needed) |
| Container Apps fallback script | Not run (Foundry worked) |
| Hot reload **inside** the hosted container | Not applicable: skills are baked into the image; a change means `az acr build` + `azd deploy` (new agent version) |

With `gpt-5`, the router sometimes answers "Check my ticket." with `clarify` (asking for the ID itself) instead of loading the ticket skill to ask. Both meet the requirement; the scripted offline model always loads the skill.
