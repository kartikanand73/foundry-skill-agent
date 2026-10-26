# Step 5: one Application Insights resource for the whole path, so a chat turn is one end-to-end trace:
#   web app (OpenTelemetry) -> APIM (App Insights logger, W3C) -> Foundry hosted agent (project monitoring)
# Prompt/answer text is not logged (APIM logs no bodies; app spans record metadata only).
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$Law = 'log-skillagent-ka2026'; $Appi = 'appi-skillagent-ka2026'
function Put-Arm($url, $obj) {   # JSON via file: az.cmd mangles ( ) ; in inline bodies
  $f = Join-Path $env:TEMP ("arm-" + [guid]::NewGuid() + ".json")
  [IO.File]::WriteAllText($f, ($obj | ConvertTo-Json -Depth 12))
  & $az rest --method put --url $url --body "@$f" --query "name" -o tsv
  Remove-Item $f
}

step "Log Analytics workspace + Application Insights (workspace-based)"
& $az monitor log-analytics workspace create -g $Rg -n $Law -l $Loc --retention-time 30 -o none
$lawId = & $az monitor log-analytics workspace show -g $Rg -n $Law --query id -o tsv
$aiUrl = "$Arm/Microsoft.Insights/components/$Appi`?api-version=2020-02-02"
Put-Arm $aiUrl @{ location = $Loc; kind = 'web'; properties = @{ Application_Type = 'web'; WorkspaceResourceId = $lawId } }
$ai = (& $az rest --method get --url $aiUrl -o json) -join "`n" | ConvertFrom-Json
$aiId = $ai.id; $cs = $ai.properties.ConnectionString; $ikey = $ai.properties.InstrumentationKey
"app insights: $aiId"

step "web app: connection string + service name (JSON file)"
$settings = @(
  @{ name = 'APPLICATIONINSIGHTS_CONNECTION_STRING'; value = $cs; slotSetting = $false },
  @{ name = 'OTEL_SERVICE_NAME'; value = 'skillagent-webapp'; slotSetting = $false }
) | ConvertTo-Json
$sf = Join-Path $env:TEMP "appi-settings.json"; [IO.File]::WriteAllText($sf, $settings)
& $az webapp config appsettings set -n $App -g $Rg --settings "@$sf" -o none
Remove-Item $sf

step "APIM: App Insights logger + API diagnostics (W3C correlation, no bodies)"
$apimBase = "$Arm/Microsoft.ApiManagement/service/$Apim"
Put-Arm "$apimBase/loggers/appinsights?api-version=2022-08-01" @{ properties = @{
  loggerType = 'applicationInsights'; resourceId = $aiId; credentials = @{ instrumentationKey = $ikey } } }
$noBody = @{ request = @{ headers = @(); body = @{ bytes = 0 } }; response = @{ headers = @(); body = @{ bytes = 0 } } }
Put-Arm "$apimBase/apis/skill-agent/diagnostics/applicationinsights?api-version=2022-08-01" @{ properties = @{
  loggerId = "$($apimBase.Replace('https://management.azure.com',''))/loggers/appinsights"
  alwaysLog = 'allErrors'; httpCorrelationProtocol = 'W3C'; verbosity = 'information'; logClientIp = $false
  metrics = $true; sampling = @{ samplingType = 'fixed'; percentage = 100 }
  frontend = $noBody; backend = $noBody } }

step "Foundry project: connect Application Insights (project monitoring -> injected into hosted agents)"
$proj = "$Arm/Microsoft.CognitiveServices/accounts/$FoundryAccount/projects/proj-default"
Put-Arm "$proj/connections/$Appi`?api-version=2025-06-01" @{ properties = @{
  category = 'AppInsights'; target = $aiId; authType = 'ApiKey'; isSharedToAll = $true
  credentials = @{ key = $cs }; metadata = @{ ApiType = 'Azure'; ResourceId = $aiId } } }

step "APPI_DONE  (redeploy the hosted agent and web app so both pick it up)"
