# Step 9: per-user saved threads in Azure Table Storage (Entra-only access, no keys).
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$Sa = 'stskillagentka2026'
step "storage account (shared key off, public blob off, TLS 1.2)"
& $az storage account create -n $Sa -g $Rg -l $Loc --sku Standard_LRS --kind StorageV2 `
    --min-tls-version TLS1_2 --allow-blob-public-access false --allow-shared-key-access false -o none
$saId = & $az storage account show -n $Sa -g $Rg --query id -o tsv
step "table 'threads' (ARM; data-plane keys are disabled)"
& $az rest --method put --url "$Arm/Microsoft.Storage/storageAccounts/$Sa/tableServices/default/tables/threads?api-version=2023-05-01" --body "{}" --query name -o tsv
step "web app identity -> Storage Table Data Contributor on this table only"
$webPid = & $az webapp identity show -n $App -g $Rg --query principalId -o tsv
& $az role assignment create --assignee-object-id $webPid --assignee-principal-type ServicePrincipal `
    --role "Storage Table Data Contributor" --scope "$saId/tableServices/default/tables/threads" -o none
step "web app settings"
$s = @(
  @{ name = 'THREAD_STORE_URL'; value = "https://$Sa.table.core.windows.net"; slotSetting = $false },
  @{ name = 'THREAD_TABLE'; value = 'threads'; slotSetting = $false },
  @{ name = 'THREAD_RETENTION_DAYS'; value = '30'; slotSetting = $false }
) | ConvertTo-Json
$f = Join-Path $env:TEMP 'thread-settings.json'; [IO.File]::WriteAllText($f, $s)
& $az webapp config appsettings set -n $App -g $Rg --settings "@$f" -o none
Remove-Item $f
step "THREADS_DONE  (redeploy the web app: 3b-deploy-code.ps1)"
