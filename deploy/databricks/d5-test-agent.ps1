# D5: end-to-end test of the Databricks Genie skill on the HOSTED agent (direct Foundry call with your token;
# the user block is what the web app would send). Checks: answer, follow-up in the same session, role denial.
. "$PSScriptRoot\..\webapp\config.ps1"
$ErrorActionPreference = 'Continue'
$tok = & $az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
$h = @{ Authorization = "Bearer $tok" }
function Ask($label, $msg, $roles, $sid) {
  $url = "$ProtocolsUrl/invocations?api-version=v1"
  if ($sid) { $url += "&agent_session_id=$sid" }
  $rl = ($roles | ForEach-Object { '"' + $_ + '"' }) -join ','
  $body = '{"message":' + ($msg | ConvertTo-Json) + ',"user":{"oid":"d5-test-user","name":"D5 test","roles":[' + $rl + ']}}'
  $t0 = Get-Date
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $h -Body $body -ContentType 'application/json' -TimeoutSec 240
    $j = $r.Content | ConvertFrom-Json
    Write-Host "`n== $label  ($([int]((Get-Date) - $t0).TotalSeconds)s)  skill=$($j.skill) tools=$(($j.tool_calls | ForEach-Object { $_.tool + ':' + $_.status }) -join ',') tokens=$($j.usage.total_tokens)"
    Write-Host $j.response
    return $r.Headers['x-agent-session-id']
  } catch { Write-Host "`n== $label -> HTTP $([int]$_.Exception.Response.StatusCode)"; return $sid }
}
$all = @('SkillAgent.User', 'SkillAgent.Orders')
$sid = Ask '1. revenue by customer 2026' 'From our orders data in Databricks, what is total revenue by customer account in 2026?' $all $null
$sid = Ask '2. follow-up (same session)' 'Which 3 products drove the most of that for Initech?' $all $sid
$null = Ask '3. open orders' 'How many Globex Inc orders are still Processing or Shipped, and what are they worth?' $all $null
$null = Ask '4. no Orders role -> denied' 'Show total order revenue by customer in Databricks' @('SkillAgent.User', 'SkillAgent.Sales') $null
step "D5_DONE"
