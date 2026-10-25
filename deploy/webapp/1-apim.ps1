# Step 1: API Management (Consumption) in front of the hosted agent.
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
step "provider"; & $az provider register -n Microsoft.ApiManagement --wait
$exists = & $az apim show -n $Apim -g $Rg --query id -o tsv 2>$null
if (-not $exists) {
  step "create APIM $Apim (Consumption)"
  & $az apim create -n $Apim -g $Rg -l $Loc --sku-name Consumption --publisher-email $PublisherEmail `
      --publisher-name $PublisherName --enable-managed-identity true -o none
}
$apimPid = & $az apim show -n $Apim -g $Rg --query identity.principalId -o tsv
step "APIM identity $apimPid -> Foundry User on $FoundryAccount"
$acct = & $az cognitiveservices account show -n $FoundryAccount -g $Rg --query id -o tsv
& $az role assignment create --assignee-object-id $apimPid --assignee-principal-type ServicePrincipal `
    --role "Foundry User" --scope $acct -o none

step "named value + API + operation"
& $az apim nv create -n $Apim -g $Rg --named-value-id foundry-agent-protocols-url --display-name foundry-agent-protocols-url --value $ProtocolsUrl -o none 2>$null
& $az apim api create -n $Apim -g $Rg --api-id skill-agent --path skill-agent --display-name "Skill Agent" `
    --service-url $ProtocolsUrl --protocols https --subscription-required true -o none
& $az apim api operation create -n $Apim -g $Rg --api-id skill-agent --operation-id chat --url-template /chat `
    --method POST --display-name Chat -o none

step "policy"
$xml = Get-Content "$PSScriptRoot\..\..\infra\apim-policy.xml" -Raw
$body = @{ properties = @{ format = 'rawxml'; value = $xml } } | ConvertTo-Json -Depth 5
$tmp = New-TemporaryFile; Set-Content $tmp $body -Encoding utf8
& $az rest --method put --url "$Arm/Microsoft.ApiManagement/service/$Apim/apis/skill-agent/operations/chat/policies/policy?api-version=2022-08-01" --body "@$tmp" -o none
Remove-Item $tmp

step "subscription for the web app -> Key Vault"
$subBody = '{"properties":{"scope":"/apis/skill-agent","displayName":"streamlit-app","state":"active"}}'
$tmp = New-TemporaryFile; Set-Content $tmp $subBody -Encoding ascii
& $az rest --method put --url "$Arm/Microsoft.ApiManagement/service/$Apim/subscriptions/streamlit-app?api-version=2022-08-01" --body "@$tmp" -o none
Remove-Item $tmp
$key = & $az rest --method post --url "$Arm/Microsoft.ApiManagement/service/$Apim/subscriptions/streamlit-app/listSecrets?api-version=2022-08-01" --query primaryKey -o tsv
$kf = New-TemporaryFile; [IO.File]::WriteAllText($kf, $key)
& $az keyvault secret set --vault-name $Kv -n apim-subscription-key --file $kf --encoding utf-8 -o none
Remove-Item $kf
"gateway: https://$Apim.azure-api.net/skill-agent/chat"
step "APIM_DONE"
