# Step 10: token budgets + ledger at the APIM layer (Table Storage, APIM managed identity).
#   tables  : tokenledger (one row per call), tokenusage (counters: user-<oid>/<yyyy-MM-dd>, app/<yyyy-MM>)
#   limits  : APIM named values token-limit-user-daily (50000) and token-limit-app-monthly (2000000)
#   policies: operation policy = identity + budget check + routing; API policy = charging (16 KiB cap each)
# Change a limit later:  az apim nv update -n <apim> -g <rg> --named-value-id token-limit-user-daily --value 75000
param([long]$UserDaily = 50000, [long]$AppMonthly = 2000000)
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$Sa = 'stskillagentka2026'
function Put-Arm($url, $obj) {
  $f = Join-Path $env:TEMP ("arm-" + [guid]::NewGuid() + ".json")
  [IO.File]::WriteAllText($f, ($obj | ConvertTo-Json -Depth 8))
  & $az rest --method put --url $url --body "@$f" --query "name" -o tsv
  Remove-Item $f
}
$saId = & $az storage account show -n $Sa -g $Rg --query id -o tsv

step "tables"
foreach ($t in 'tokenledger', 'tokenusage') {
  & $az rest --method put --url "$Arm/Microsoft.Storage/storageAccounts/$Sa/tableServices/default/tables/$t`?api-version=2023-05-01" --body "{}" --query name -o tsv
}

step "APIM identity -> Storage Table Data Contributor on the two tables only"
$apimPid = & $az apim show -n $Apim -g $Rg --query identity.principalId -o tsv
foreach ($t in 'tokenledger', 'tokenusage') {
  & $az role assignment create --assignee-object-id $apimPid --assignee-principal-type ServicePrincipal `
      --role "Storage Table Data Contributor" --scope "$saId/tableServices/default/tables/$t" -o none 2>$null
}

step "you -> Storage Table Data Reader on the account (to run ledger_report.py / browse in the portal)"
$me = & $az ad signed-in-user show --query id -o tsv
& $az role assignment create --assignee-object-id $me --assignee-principal-type User `
    --role "Storage Table Data Reader" --scope $saId -o none 2>$null

step "named values"
$nv = "$Arm/Microsoft.ApiManagement/service/$Apim/namedValues"
Put-Arm "$nv/token-ledger-table-url?api-version=2022-08-01" @{ properties = @{ displayName = 'token-ledger-table-url'; value = "https://$Sa.table.core.windows.net"; secret = $false } }
Put-Arm "$nv/token-limit-user-daily?api-version=2022-08-01" @{ properties = @{ displayName = 'token-limit-user-daily'; value = "$UserDaily"; secret = $false } }
Put-Arm "$nv/token-limit-app-monthly?api-version=2022-08-01" @{ properties = @{ displayName = 'token-limit-app-monthly'; value = "$AppMonthly"; secret = $false } }

step "API-level policy (charging)"
$xml = [IO.File]::ReadAllText((Resolve-Path "$PSScriptRoot\..\..\infra\apim-api-policy.xml"))
$body = @{ properties = @{ format = 'rawxml'; value = $xml } } | ConvertTo-Json -Depth 5
$tmp = New-TemporaryFile; [IO.File]::WriteAllText($tmp, $body)
& $az rest --method put --url "$Arm/Microsoft.ApiManagement/service/$Apim/apis/skill-agent/policies/policy?api-version=2022-08-01" --body "@$tmp" --query "properties.format" -o tsv
Remove-Item $tmp

step "operation policy (identity + budget check + routing)"
& "$PSScriptRoot\1b-apim-policy.ps1"
step "LEDGER_DONE  (role assignments can take a few minutes; until then responses say x-token-ledger: degraded)"
