# Snowflake skill through APIM: question + follow-up in the same hosted session.
. "$PSScriptRoot\config.ps1"
$key = & $az keyvault secret show --vault-name $Kv -n apim-subscription-key --query value -o tsv
$h = @{ 'Ocp-Apim-Subscription-Key' = $key; 'Content-Type' = 'application/json' }
$url = "https://$Apim.azure-api.net/skill-agent/chat"
$r1 = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $url -Headers $h -TimeoutSec 150 `
      -Body '{"message":"What were total paid claims in Q1 2026 in Snowflake?"}'
"HTTP $($r1.StatusCode)"; [Text.Encoding]::UTF8.GetString($r1.RawContentStream.ToArray())
$sid = $r1.Headers['x-agent-session-id']
$r2 = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$url`?agent_session_id=$sid" -Headers $h -TimeoutSec 150 `
      -Body '{"message":"And the denial rate for that quarter?"}'
"HTTP $($r2.StatusCode)"; [Text.Encoding]::UTF8.GetString($r2.RawContentStream.ToArray())
step "SF_APIM_TEST_DONE"
