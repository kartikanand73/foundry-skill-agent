# Step 7: APIM accepts only the web app's managed identity (validate-jwt) in addition to the key.
#   1. App registration "skillagent-api-ka2026" = the API's audience (api://<app id>)
#   2. APIM named values: tenant-id, skill-agent-api-audience, webapp-principal-id
#   3. Web app setting APIM_TOKEN_AUDIENCE (the app then sends a Bearer token from its identity)
#   4. Re-apply the APIM policy (validate-jwt)
# Rerun-safe. Order matters: the web app must be redeployed with token support (3b) BEFORE step 4,
# otherwise the live app gets 401s. This script does 1-3, then 3b, then 4.
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$ApiApp = 'skillagent-api-ka2026'
function Put-Arm($url, $obj) {
  $f = Join-Path $env:TEMP ("arm-" + [guid]::NewGuid() + ".json")
  [IO.File]::WriteAllText($f, ($obj | ConvertTo-Json -Depth 8))
  & $az rest --method put --url $url --body "@$f" --query "name" -o tsv
  Remove-Item $f
}

step "app registration for the API audience"
$apiAppId = & $az ad app list --display-name $ApiApp --query "[0].appId" -o tsv
if (-not $apiAppId) {
  $apiAppId = & $az ad app create --display-name $ApiApp --sign-in-audience AzureADMyOrg --query appId -o tsv
}
& $az ad app update --id $apiAppId --identifier-uris "api://$apiAppId" -o none
& $az ad sp create --id $apiAppId -o none 2>$null
$aud = "api://$apiAppId"
"audience: $aud"

step "APIM named values"
$tenant = & $az account show --query tenantId -o tsv
$webPid = & $az webapp identity show -n $App -g $Rg --query principalId -o tsv
$nv = "$Arm/Microsoft.ApiManagement/service/$Apim/namedValues"
Put-Arm "$nv/tenant-id?api-version=2022-08-01" @{ properties = @{ displayName = 'tenant-id'; value = $tenant; secret = $false } }
Put-Arm "$nv/skill-agent-api-audience?api-version=2022-08-01" @{ properties = @{ displayName = 'skill-agent-api-audience'; value = $aud; secret = $false } }
Put-Arm "$nv/webapp-principal-id?api-version=2022-08-01" @{ properties = @{ displayName = 'webapp-principal-id'; value = $webPid; secret = $false } }

step "web app: APIM_TOKEN_AUDIENCE"
$sf = Join-Path $env:TEMP "jwt-settings.json"
# Windows PowerShell 5 has no -AsArray: build the one-element JSON array by hand
[IO.File]::WriteAllText($sf, '[' + (@{ name = 'APIM_TOKEN_AUDIENCE'; value = $aud; slotSetting = $false } | ConvertTo-Json) + ']')
& $az webapp config appsettings set -n $App -g $Rg --settings "@$sf" -o none
Remove-Item $sf

step "redeploy web app code (sends the token)"
& "$PSScriptRoot\3b-deploy-code.ps1"

step "apply APIM policy with validate-jwt"
& "$PSScriptRoot\1b-apim-policy.ps1"
step "JWT_DONE"
