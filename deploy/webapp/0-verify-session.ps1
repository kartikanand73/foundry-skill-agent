# Step 0: confirm how hosted sessions behave when the invocations endpoint is called directly
# (same call APIM will make). Uses your az login token for https://ai.azure.com.
. "$PSScriptRoot\config.ps1"
$tok = & $az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
$h = @{ Authorization = "Bearer $tok"; 'Content-Type' = 'application/json' }
$url = "$ProtocolsUrl/invocations?api-version=v1"
step "call 1 (no session)"
$r1 = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $h -Body '{"message":"Check my ticket."}'
"HTTP $($r1.StatusCode)"; $r1.Headers.GetEnumerator() | ? { $_.Key -match 'session|agent' } | % { "$($_.Key): $($_.Value)" }
$r1.Content
$sid = $r1.Headers['x-agent-session-id']
step "call 2 (agent_session_id=$sid)"
$r2 = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$url&agent_session_id=$sid" -Headers $h -Body '{"message":"INC1003"}'
"HTTP $($r2.StatusCode)"; $r2.Content
step "VERIFY_DONE"
