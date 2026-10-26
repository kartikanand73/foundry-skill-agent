# Rebuild the agent image in ACR (no local Docker) and deploy a new hosted-agent version with azd.
# usage: deploy\hosted\redeploy.ps1 -Tag v3
param([Parameter(Mandatory)][string]$Tag)
. "$PSScriptRoot\..\webapp\config.ps1"
$ErrorActionPreference = 'Continue'
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root
$acr = 'skillagentacrka2026'
step "acr build $acr/skill-agent:$Tag"
& $az acr build -r $acr -t "skill-agent:$Tag" . --no-logs -o none
step "azd env (non-secret Snowflake + Databricks settings; the PAT stays in Key Vault)"
$envFile = Get-Content "$root\.env"
foreach ($k in 'SNOWFLAKE_ACCOUNT_URL','SNOWFLAKE_AGENT_DATABASE','SNOWFLAKE_AGENT_SCHEMA','SNOWFLAKE_AGENT_NAME','DATABRICKS_HOST','GENIE_SPACE_ID') {
  $v = (($envFile | Where-Object { $_ -like "$k=*" }) -replace "^$k=", '').Trim()
  if ($v) { azd env set $k $v }
}
azd env set AGENT_IMAGE "$acr.azurecr.io/skill-agent:$Tag"
step "azd deploy"
azd deploy --no-prompt
azd env get-value AGENT_SKILL_AGENT_VERSION
azd env get-value AGENT_SKILL_AGENT_INSTANCE_IDENTITY_PRINCIPAL_ID
step "REDEPLOY_DONE"
