# Step 2: call the agent through APIM (new session, then follow-up in the same session).
# NOTE: works only before step 7 (validate-jwt). After that, use 2c-test-apim-locked.ps1 and test through the web app.
. "$PSScriptRoot\config.ps1"
$key = & $az keyvault secret show --vault-name $Kv -n apim-subscription-key --query value -o tsv
$h = @{ 'Ocp-Apim-Subscription-Key' = $key; 'Content-Type' = 'application/json' }
$url = "https://$Apim.azure-api.net/skill-agent/chat"
$r1 = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $h -Body '{"message":"Check my ticket."}'
"HTTP $($r1.StatusCode) session=$($r1.Headers['x-agent-session-id'])"; $r1.Content
$sid = $r1.Headers['x-agent-session-id']
$r2 = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$url`?agent_session_id=$sid" -Headers $h -Body '{"message":"INC1003"}'
"HTTP $($r2.StatusCode)"; $r2.Content
try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Body '{"message":"x"}' -ContentType 'application/json' | Out-Null; "NO-KEY: unexpectedly allowed" }
catch { "NO-KEY: rejected ($($_.Exception.Response.StatusCode.value__))" }
step "APIM_TEST_DONE"
