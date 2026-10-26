# The hosted agent's own endpoint must refuse callers without an Entra token (and callers without a role).
. "$PSScriptRoot\config.ps1"
$url = "$ProtocolsUrl/invocations?api-version=v1"
function Try-Call($label, $headers) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $headers -Body '{"message":"Status of INC1001?"}' -ContentType 'application/json' -TimeoutSec 60
        "$label -> $($r.StatusCode)  body: $($r.Content.Substring(0, [Math]::Min(160, $r.Content.Length)))" }
  catch { "$label -> $([int]$_.Exception.Response.StatusCode)" }
}
Try-Call "no token                         " @{}
$tok = & $az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
Try-Call "your own token (Foundry User), no signed-in user in body" @{ Authorization = "Bearer $tok" }
step "FOUNDRY_DIRECT_DONE"
