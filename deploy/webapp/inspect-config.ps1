# Read-only: print how every piece is configured right now (names and settings, never secret values).
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$armHdr = @{ Authorization = "Bearer " + (& $az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv) }
$graphHdr = @{ Authorization = "Bearer " + (& $az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv) }
function A($path, $ver) { Invoke-RestMethod -Headers $armHdr -Uri "$Arm/$path`?api-version=$ver" }
function G($path) { Invoke-RestMethod -Headers $graphHdr -Uri "https://graph.microsoft.com/v1.0/$path" }
function Show($label, $value) { "  {0,-40} {1}" -f $label, $value }

"== 1. Web app $App"
$site = A "Microsoft.Web/sites/$App" '2023-12-01'
Show 'httpsOnly' $site.properties.httpsOnly
Show 'managed identity' $site.identity.type
$auth = A "Microsoft.Web/sites/$App/config/authsettingsV2" '2023-12-01'
Show 'auth enabled' $auth.properties.platform.enabled
Show 'unauthenticated action' $auth.properties.globalValidation.unauthenticatedClientAction
Show 'identity provider' ($auth.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer)
$set = Invoke-RestMethod -Method Post -Headers $armHdr -Uri "$Arm/Microsoft.Web/sites/$App/config/appsettings/list?api-version=2023-12-01"
foreach ($p in $set.properties.PSObject.Properties) {
  $v = if ($p.Value -like '@Microsoft.KeyVault*') { 'Key Vault reference' } elseif ($p.Name -match 'SECRET|KEY|CONNECTION') { '(set, hidden)' } else { $p.Value }
  Show "setting $($p.Name)" $v
}

"`n== 2. Entra app roles and assignments"
$authAppId = $auth.properties.identityProviders.azureActiveDirectory.registration.clientId
$app = G "applications(appId='$authAppId')"
Show 'sign-in app' "$($app.displayName) ($authAppId)"
foreach ($r in $app.appRoles) { Show "role" "$($r.value)  enabled=$($r.isEnabled)" }
$sp = G "servicePrincipals(appId='$authAppId')"
Show 'assignment required to sign in' $sp.appRoleAssignmentRequired
$asg = G "servicePrincipals/$($sp.id)/appRoleAssignedTo"
foreach ($x in $asg.value) { $rn = ($app.appRoles | Where-Object id -eq $x.appRoleId).value; Show "assigned" "$($x.principalDisplayName) -> $rn" }

"`n== 3. APIM $Apim"
$api = A "Microsoft.ApiManagement/service/$Apim/apis/skill-agent" '2022-08-01'
Show 'API path / subscription required' "$($api.properties.path) / $($api.properties.subscriptionRequired)"
$nvs = A "Microsoft.ApiManagement/service/$Apim/namedValues" '2022-08-01'
foreach ($n in $nvs.value) { Show "named value $($n.name)" ($(if ($n.properties.secret) { '(secret)' } else { $n.properties.value })) }
foreach ($scope in @(@{ n = 'API policy'; p = "apis/skill-agent/policies/policy" }, @{ n = 'operation policy'; p = "apis/skill-agent/operations/chat/policies/policy" })) {
  $pol = (A "Microsoft.ApiManagement/service/$Apim/$($scope.p)" '2022-08-01').properties.value
  $has = @('validate-jwt', 'rate-limit', 'tokenusage', 'tokenledger', 'authentication-managed-identity', 'return-response') | Where-Object { $pol -match $_ }
  Show $scope.n ("{0} chars; contains: {1}" -f $pol.Length, ($has -join ', '))
}
$logger = A "Microsoft.ApiManagement/service/$Apim/apis/skill-agent/diagnostics/applicationinsights" '2022-08-01'
Show 'App Insights diagnostics' "sampling $($logger.properties.sampling.percentage)%, correlation $($logger.properties.httpCorrelationProtocol), body bytes logged $($logger.properties.frontend.request.body.bytes)"

"`n== 4. Foundry $FoundryAccount / gpt-5 guardrail"
$acct = "Microsoft.CognitiveServices/accounts/$FoundryAccount"
$dep = A "$acct/deployments/gpt-5" '2025-06-01'
Show 'gpt-5 guardrail (raiPolicyName)' $dep.properties.raiPolicyName
Show 'gpt-5 sku / capacity' "$($dep.sku.name) / $($dep.sku.capacity)"
$rai = A "$acct/raiPolicies/$($dep.properties.raiPolicyName)" '2025-06-01'
Show 'guardrail mode / base' "$($rai.properties.mode) / $($rai.properties.basePolicyName)"
foreach ($f in $rai.properties.contentFilters) { Show "  $($f.source) $($f.name)" ("threshold={0} blocking={1} enabled={2}" -f $f.severityThreshold, $f.blocking, $f.enabled) }
$conns = A "$acct/projects/proj-default/connections" '2025-06-01'
foreach ($c in $conns.value) { Show "project connection" "$($c.name) ($($c.properties.category))" }

"`n== 5. Storage stskillagentka2026"
$sa = A "Microsoft.Storage/storageAccounts/stskillagentka2026" '2023-05-01'
Show 'shared key access allowed' $sa.properties.allowSharedKeyAccess
Show 'public blob access / min TLS' "$($sa.properties.allowBlobPublicAccess) / $($sa.properties.minimumTlsVersion)"
$tables = A "Microsoft.Storage/storageAccounts/stskillagentka2026/tableServices/default/tables" '2023-05-01'
Show 'tables' (($tables.value | ForEach-Object name) -join ', ')
$ra = & $az role assignment list --scope $sa.id --include-inherited false --all --query "[].{who:principalName, type:principalType, role:roleDefinitionName, scope:scope}" -o json | ConvertFrom-Json
foreach ($r in $ra) { Show "role" ("{0} [{1}] {2} on ...{3}" -f $r.role, $r.type, $r.who, ($r.scope -split 'stskillagentka2026')[-1]) }

"`n== 6. Key Vault $Kv"
$kvr = A "Microsoft.KeyVault/vaults/$Kv" '2023-07-01'
Show 'RBAC authorization' $kvr.properties.enableRbacAuthorization
Show 'secret names' ((& $az keyvault secret list --vault-name $Kv --query "[].name" -o tsv) -join ', ')
step "INSPECT_DONE"
