# After step 7: the subscription key alone, or a token from any identity other than the web app, must be 401.
. "$PSScriptRoot\config.ps1"
$key = & $az keyvault secret show --vault-name $Kv -n apim-subscription-key --query value -o tsv
$aud = & $az rest --method get --url "$Arm/Microsoft.ApiManagement/service/$Apim/namedValues/skill-agent-api-audience?api-version=2022-08-01" --query properties.value -o tsv
$url = "https://$Apim.azure-api.net/skill-agent/chat"
function Try-Call($label, $headers) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $headers -Body '{"message":"Status of INC1001?"}' -ContentType 'application/json' -TimeoutSec 60
        "$label -> $($r.StatusCode)  (UNEXPECTED: allowed)" }
  catch { "$label -> $([int]$_.Exception.Response.StatusCode)" }
}
Try-Call "no key, no token        " @{}
Try-Call "key only                " @{ 'Ocp-Apim-Subscription-Key' = $key }
$userTok = & $az account get-access-token --resource $aud --query accessToken -o tsv 2>$null
if ($userTok) { Try-Call "key + YOUR user token   " @{ 'Ocp-Apim-Subscription-Key' = $key; Authorization = "Bearer $userTok" } }
else { "key + your user token    -> (could not get a user token for $aud; that is also fine)" }
step "LOCKED_TEST_DONE  (expected: 401 on every line; the web app is the only allowed caller)"
