# foundry-skill-agent

A small, working Python agent that **picks an instruction package ("skill") per message, loads only that skill, exposes only that skill's tool, and runs real Python tools** against local synthetic JSON data. One core runtime is shared by a CLI and a Microsoft Foundry Hosted Agent entry point (with an Azure Container Apps fallback).

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
  tools.py      lookup_policy, get_ticket_status, schemas, argument validation, allowlist
  llm.py        OpenAI SDK → Azure OpenAI v1 endpoint with Entra ID tokens
  config.py     env-based settings
  trace.py      [trace] lines on stderr (never credentials)
  fake_llm.py   OFFLINE scripted stand-in model for tests/demos only
skills/hr-policy/SKILL.md     skills/it-ticket/SKILL.md
data/policies.json            data/tickets.json
cli.py          local chat
main.py         Foundry Hosted Agent entry point (Invocations protocol, port 8088)
scripts/demo.py required demo cases + hot-reload demo
tests/          16 offline tests
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

Model usage is billed on your existing `gpt-5` deployment. Each message is 1 call (unsupported/clarify) or 3 calls (route → tool request → answer). `gpt-5` costs more per token than a mini model; for a cheaper demo, deploy `gpt-5-mini` (or similar) and set `MODEL_DEPLOYMENT_NAME`. Check the Azure pricing calculator for current rates.

Container Apps fallback (not used): RG + ACR Basic + consumption environment that scales to zero.

## 6. Delete

```bash
# 1. the hosted agent (all versions)
azd ai agent delete skill-agent
# 2. the container registry
az acr delete -n <acr> -g <rg> --yes
# 3. optional: the agent's role assignment on the account (gone with the agent identity anyway)
```

`azd down` is **not** needed here and would not delete your existing project. Your Foundry account, project, `gpt-5` deployment and resource group `DB` are left untouched by both commands.

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
