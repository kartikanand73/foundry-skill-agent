# Re-apply only the APIM policy (after editing infra/apim-policy.xml).
. "$PSScriptRoot\config.ps1"
$xml = [IO.File]::ReadAllText((Resolve-Path "$PSScriptRoot\..\..\infra\apim-policy.xml"))
$body = @{ properties = @{ format = 'rawxml'; value = $xml } } | ConvertTo-Json -Depth 5
$tmp = New-TemporaryFile; [IO.File]::WriteAllText($tmp, $body)
& $az rest --method put --url "$Arm/Microsoft.ApiManagement/service/$Apim/apis/skill-agent/operations/chat/policies/policy?api-version=2022-08-01" --body "@$tmp" --query "properties.format" -o tsv
Remove-Item $tmp
step "POLICY_DONE"
