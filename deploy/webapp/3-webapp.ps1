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
step "settings (JSON file: az.cmd mangles the ( ) ; in Key Vault references)"
$settings = @(
  @{ name = 'APIM_BASE_URL'; value = "https://$Apim.azure-api.net/skill-agent"; slotSetting = $false },
  @{ name = 'APIM_SUBSCRIPTION_KEY'; value = "@Microsoft.KeyVault(VaultName=$Kv;SecretName=apim-subscription-key)"; slotSetting = $false },
  @{ name = 'SCM_DO_BUILD_DURING_DEPLOYMENT'; value = 'true'; slotSetting = $false }
) | ConvertTo-Json
$sf = Join-Path $env:TEMP "appsettings-$App.json"; [IO.File]::WriteAllText($sf, $settings)
& $az webapp config appsettings set -n $App -g $Rg --settings "@$sf" -o none
Remove-Item $sf
& $az webapp config set -n $App -g $Rg --web-sockets-enabled true --always-on true --http20-enabled true `
    --startup-file "python -m streamlit run app.py --server.port 8000 --server.address 0.0.0.0" -o none
& $az webapp update -n $App -g $Rg --https-only true -o none
& "$PSScriptRoot\3b-deploy-code.ps1"
step "WEBAPP_DONE"
