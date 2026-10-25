# Step 4: require Entra sign-in (App Service authentication) - only users in this tenant.
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
& $az config set extension.use_dynamic_install=yes_without_prompt extension.dynamic_install_allow_preview=true -o none
$tenant = & $az account show --query tenantId -o tsv
$redirect = "https://$App.azurewebsites.net/.auth/login/aad/callback"
step "app registration"
$appId = & $az ad app list --display-name "$App-auth" --query "[0].appId" -o tsv   # rerun-safe
if (-not $appId) {
  $appId = & $az ad app create --display-name "$App-auth" --sign-in-audience AzureADMyOrg `
      --web-redirect-uris $redirect --enable-id-token-issuance true --query appId -o tsv
}
"appId: $appId"
& $az ad sp create --id $appId -o none 2>$null
$secret = & $az ad app credential reset --id $appId --display-name easyauth --years 1 --query password -o tsv
& $az webapp config appsettings set -n $App -g $Rg -o none --settings "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET=$secret"
step "enable auth (authsettingsV2 via ARM; az webapp auth refuses apps still on the v1 format)"
$body = @{ properties = @{
  platform = @{ enabled = $true }
  globalValidation = @{ requireAuthentication = $true; unauthenticatedClientAction = 'RedirectToLoginPage'
                        redirectToProvider = 'azureactivedirectory' }
  identityProviders = @{ azureActiveDirectory = @{ enabled = $true
      registration = @{ openIdIssuer = "https://login.microsoftonline.com/$tenant/v2.0"; clientId = $appId
                        clientSecretSettingName = 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET' }
      validation = @{ allowedAudiences = @("api://$appId", $appId) } } }
  login = @{ tokenStore = @{ enabled = $true } }
  httpSettings = @{ requireHttps = $true }
} } | ConvertTo-Json -Depth 10
$bf = Join-Path $env:TEMP "authv2-$App.json"; [IO.File]::WriteAllText($bf, $body)
& $az rest --method put --url "$Arm/Microsoft.Web/sites/$App/config/authsettingsV2?api-version=2022-03-01" `
    --body "@$bf" --query "properties.globalValidation.unauthenticatedClientAction" -o tsv
Remove-Item $bf
step "AUTH_DONE  https://$App.azurewebsites.net"
