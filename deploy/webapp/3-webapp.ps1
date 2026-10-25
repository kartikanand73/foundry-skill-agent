# Step 3: App Service (Linux B1) running the Streamlit app; APIM key via Key Vault reference.
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
step "plan + app"
& $az appservice plan create -n $Plan -g $Rg -l $Loc --is-linux --sku B1 -o none
& $az webapp create -n $App -g $Rg -p $Plan --runtime "PYTHON:3.12" -o none
$appPid = & $az webapp identity assign -n $App -g $Rg --query principalId -o tsv
$kvId = & $az keyvault show -n $Kv -g $Rg --query id -o tsv
& $az role assignment create --assignee-object-id $appPid --assignee-principal-type ServicePrincipal `
    --role "Key Vault Secrets User" --scope $kvId -o none
step "settings"
& $az webapp config appsettings set -n $App -g $Rg -o none --settings `
    "APIM_BASE_URL=https://$Apim.azure-api.net/skill-agent" `
    "APIM_SUBSCRIPTION_KEY=@Microsoft.KeyVault(VaultName=$Kv;SecretName=apim-subscription-key)" `
    "SCM_DO_BUILD_DURING_DEPLOYMENT=true"
& $az webapp config set -n $App -g $Rg --web-sockets-enabled true --always-on true --http20-enabled true `
    --startup-file "python -m streamlit run app.py --server.port 8000 --server.address 0.0.0.0" -o none
& $az webapp update -n $App -g $Rg --https-only true -o none
step "zip deploy webapp/"
$src = Resolve-Path "$PSScriptRoot\..\..\webapp"
$zip = Join-Path $env:TEMP "webapp-$(Get-Date -Format yyyyMMddHHmmss).zip"
Compress-Archive -Path "$src\*", "$src\.streamlit" -DestinationPath $zip -Force
& $az webapp deploy -n $App -g $Rg --src-path $zip --type zip -o none
Remove-Item $zip
"site: https://$App.azurewebsites.net"
step "WEBAPP_DONE"
