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
step "enable auth"
& $az webapp auth microsoft update -n $App -g $Rg --client-id $appId `
    --client-secret-setting-name MICROSOFT_PROVIDER_AUTHENTICATION_SECRET `
    --issuer "https://login.microsoftonline.com/$tenant/v2.0" --yes -o none
& $az webapp auth update -n $App -g $Rg --enabled true --action RedirectToLoginPage `
    --redirect-provider azureactivedirectory -o none
step "AUTH_DONE  https://$App.azurewebsites.net"
