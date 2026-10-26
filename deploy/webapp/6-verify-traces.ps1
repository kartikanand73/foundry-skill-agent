# Step 6: check what reached Application Insights (last 30 min) and whether turns are one trace end to end.
. "$PSScriptRoot\config.ps1"
$wsId = & $az monitor log-analytics workspace show -g $Rg -n log-skillagent-ka2026 --query customerId -o tsv
function Q($kql) {
  $f = Join-Path $env:TEMP ("q-" + [guid]::NewGuid() + ".json")
  [IO.File]::WriteAllText($f, (@{ query = $kql } | ConvertTo-Json))
  $r = (& $az rest --method post --url "https://api.loganalytics.io/v1/workspaces/$wsId/query" `
        --resource https://api.loganalytics.io --body "@$f" -o json) -join "`n" | ConvertFrom-Json
  Remove-Item $f
  $t = $r.tables[0]; $cols = $t.columns.name
  foreach ($row in $t.rows) { ($cols | ForEach-Object -Begin { $i = 0 } -Process { "$_=$($row[$i])"; $i++ }) -join ' | ' }
}
step "telemetry by component (last 30 min)"
Q @"
union AppRequests, AppDependencies, AppTraces
| where TimeGenerated > ago(30m)
| summarize n=count() by AppRoleName, Type
| order by AppRoleName asc
"@
step "agent spans"
Q @"
AppDependencies | where TimeGenerated > ago(30m) and Name startswith 'skill_agent.'
| project TimeGenerated, Name, skill=tostring(Properties['skill_agent.skill']), tool=tostring(Properties['skill_agent.tool']),
          status=tostring(Properties['skill_agent.status']), DurationMs, OperationId
| order by TimeGenerated desc | take 12
"@
step "end-to-end: components per trace (OperationId)"
Q @"
union AppRequests, AppDependencies
| where TimeGenerated > ago(30m)
| summarize components=make_set(AppRoleName), spans=count() by OperationId
| where array_length(components) > 1
| take 10
"@
step "VERIFY_DONE"
