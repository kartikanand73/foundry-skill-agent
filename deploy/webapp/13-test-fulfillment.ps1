# 13: end-to-end test of the fulfillment-risk skill on the HOSTED agent (direct Foundry call with your token;
# the user block is what the web app would send). Checks: answer, follow-up in the same session, role denial.
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$tok = & $az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
$h = @{ Authorization = "Bearer $tok" }
function Ask($label, $msg, $roles, $sid) {
  $url = "$ProtocolsUrl/invocations?api-version=v1"
  if ($sid) { $url += "&agent_session_id=$sid" }
  $rl = ($roles | ForEach-Object { '"' + $_ + '"' }) -join ','
  $body = '{"message":' + ($msg | ConvertTo-Json) + ',"user":{"oid":"00000000-0000-0000-0000-0000000000d5","name":"D5 test","roles":[' + $rl + ']}}'
  $t0 = Get-Date
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $h -Body $body -ContentType 'application/json' -TimeoutSec 240
    $j = $r.Content | ConvertFrom-Json
    Write-Host "`n== $label  ($([int]((Get-Date) - $t0).TotalSeconds)s)  skill=$($j.skill) tools=$(($j.tool_calls | ForEach-Object { $_.tool + ':' + $_.status }) -join ',') tokens=$($j.usage.total_tokens)"
    Write-Host $j.response
    return $r.Headers['x-agent-session-id']
  } catch { Write-Host "`n== $label -> HTTP $([int]$_.Exception.Response.StatusCode)"; return $sid }
}
$all = @('SkillAgent.User', 'SkillAgent.FulfillmentRisk')
$sid = Ask '1. who is at risk' 'Which customers have open orders we cannot fulfil from current inventory?' $all $null
$sid = Ask '2. follow-up: why Globex' 'Why is Globex at risk? Explain the order.' $all $sid
$null = Ask '3. specific order' 'Explain fulfillment for order SO-101991' $all $null
$null = Ask '4. no role -> denied' 'Which customers have fulfillment risk?' @('SkillAgent.User', 'SkillAgent.Orders') $null
step "FR_TEST_DONE"
