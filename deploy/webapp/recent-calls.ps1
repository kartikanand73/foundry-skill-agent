# Recent requests, dependencies and exceptions across all components (last N minutes).
param([int]$Minutes = 20)
. "$PSScriptRoot\config.ps1"
$wsId = & $az monitor log-analytics workspace show -g $Rg -n log-skillagent-ka2026 --query customerId -o tsv
$kql = @"
union AppRequests, AppDependencies, AppExceptions
| where TimeGenerated > ago(${Minutes}m)
| extend what = coalesce(Name, ProblemId), code = tostring(ResultCode), ms = round(DurationMs)
| project TimeGenerated, AppRoleName, Type, what, code, ms, Success, OperationId, msg = substring(tostring(OuterMessage), 0, 160)
| order by TimeGenerated desc
| take 40
"@
$f = Join-Path $env:TEMP 'q.json'; [IO.File]::WriteAllText($f, (@{ query = $kql } | ConvertTo-Json))
$r = (& $az rest --method post --url "https://api.loganalytics.io/v1/workspaces/$wsId/query" --resource https://api.loganalytics.io --body "@$f" -o json) -join "`n" | ConvertFrom-Json
foreach ($row in $r.tables[0].rows) { "{0:HH:mm:ss} {1,-30} {2,-16} {3,-45} {4,-5} {5,7}ms ok={6} op={7} {8}" -f [datetime]$row[0], $row[1], $row[2], $row[3], $row[4], $row[5], $row[6], $row[7].Substring(0,8), $row[8] }
