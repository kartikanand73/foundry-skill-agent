# Step 8: Entra app roles on the web app's sign-in registration, assigned to you.
# After sign-in, the ID token carries a `roles` claim; App Service authentication passes it to the
# Streamlit app (X-MS-CLIENT-PRINCIPAL), which shows it under your name and sends it to the agent.
# Rerun-safe (fixed role ids; existing assignments are skipped). Sign out and back in afterwards:
#   https://<app>.azurewebsites.net/.auth/logout
#   -Upn someone@tenant   assign to that user instead of you;  -Roles 'SkillAgent.User','SkillAgent.Orders' to limit
param([string]$Upn = '', [string[]]$Roles = @())
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$authApp = & $az ad app list --display-name "$App-auth" --query "[0].appId" -o tsv
$spId = & $az ad sp show --id $authApp --query id -o tsv
"auth app: $authApp  service principal: $spId"

$roles = @(
  @{ v = 'SkillAgent.User';      id = '4a05d3af-3eb2-5fba-985d-6138c9471913'; d = 'Can chat with the skill agent' },
  @{ v = 'SkillAgent.HR';        id = 'dee46908-a144-5ab4-a869-c53e6ff949c2'; d = 'HR policy questions' },
  @{ v = 'SkillAgent.IT';        id = '28787290-4f59-533f-bd48-ea7429045269'; d = 'IT ticket questions' },
  @{ v = 'SkillAgent.Sales';     id = 'b2566cfb-361a-5dfd-b4ea-9d2f276942d5'; d = 'Salesforce CRM questions' },
  @{ v = 'SkillAgent.Analytics'; id = 'a396c75a-c14f-52e1-ad82-609956e22379'; d = 'Snowflake analytics questions' },
  @{ v = 'SkillAgent.Orders';    id = 'a7990314-4f01-5c06-a2f7-155b24add397'; d = 'Databricks sales-orders questions (Genie)' },
  @{ v = 'SkillAgent.Admin';     id = '1bc665f4-ca8b-597b-aaad-ebd553c5d190'; d = 'Administers the skill agent' }
)
step "define app roles"
$def = $roles | ForEach-Object { @{ allowedMemberTypes = @('User'); description = $_.d; displayName = $_.v; id = $_.id; isEnabled = $true; value = $_.v } }
$f = Join-Path $env:TEMP 'approles.json'; [IO.File]::WriteAllText($f, ($def | ConvertTo-Json -Depth 5))
& $az ad app update --id $authApp --app-roles "@$f" -o none
Remove-Item $f

if ($Upn) { $me = & $az ad user show --id $Upn --query id -o tsv; step "assign roles to $Upn ($me)" }
else { $me = & $az ad signed-in-user show --query id -o tsv; step "assign all roles to the signed-in Azure CLI user (demo)" }
if ($Roles.Count) { $roles = $roles | Where-Object { $Roles -contains $_.v } }
$have = (& $az rest --method get --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" --query "value[?principalId=='$me'].appRoleId" -o tsv)
foreach ($r in $roles) {
  if ($have -contains $r.id) { "  $($r.v): already assigned"; continue }
  $b = Join-Path $env:TEMP 'assign.json'
  [IO.File]::WriteAllText($b, (@{ principalId = $me; resourceId = $spId; appRoleId = $r.id } | ConvertTo-Json))
  & $az rest --method post --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" --body "@$b" --query "appRoleId" -o none
  Remove-Item $b
  "  $($r.v): assigned"
}
step "ROLES_DONE  (sign out and back in: https://$App.azurewebsites.net/.auth/logout)"
