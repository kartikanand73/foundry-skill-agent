# D1: Azure Databricks workspace (Premium: needed for Unity Catalog + SQL warehouses + Genie; no fixed fee).
. "$PSScriptRoot\..\webapp\config.ps1"
$ErrorActionPreference = 'Continue'
$Ws = 'dbw-skillagent-ka2026'
$mrg = "/subscriptions/$Sub/resourceGroups/databricks-rg-$Ws"
$url = "$Arm/Microsoft.Databricks/workspaces/$Ws`?api-version=2024-05-01"
step "create workspace $Ws (premium, $Loc)"
$f = Join-Path $env:TEMP 'dbw.json'
[IO.File]::WriteAllText($f, (@{ location = $Loc; sku = @{ name = 'premium' }; properties = @{ managedResourceGroupId = $mrg } } | ConvertTo-Json -Depth 5))
& $az rest --method put --url $url --body "@$f" --query "properties.provisioningState" -o tsv
Remove-Item $f
for ($i = 0; $i -lt 40; $i++) {
  $st = & $az rest --method get --url $url --query "properties.provisioningState" -o tsv
  "  $(Get-Date -Format HH:mm:ss) $st"
  if ($st -in 'Succeeded', 'Failed') { break }
  Start-Sleep 20
}
& $az rest --method get --url $url --query "{state:properties.provisioningState, url:properties.workspaceUrl, id:properties.workspaceId}" -o json
step "WORKSPACE_DONE"
