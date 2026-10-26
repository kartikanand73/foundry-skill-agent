# Step 3b: (re)deploy only the Streamlit code to the existing web app.
# Deploys asynchronously and polls Kudu's deployment status: the synchronous `az webapp deploy` often
# returns HTTP 504 on B1 while the Oryx build is still running (and a retry mid-build can break it).
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
step "zip webapp/ (Python zipfile: Unix permissions + forward slashes)"
$src = Resolve-Path "$PSScriptRoot\..\..\webapp"
$zip = Join-Path $env:TEMP "webapp-$(Get-Date -Format yyyyMMddHHmmss).zip"
$py = Resolve-Path "$PSScriptRoot\..\..\.venv\Scripts\python.exe" -ErrorAction SilentlyContinue
if (-not $py) { $py = "python" }
& $py "$PSScriptRoot\make_zip.py" $src $zip
step "zip deploy (async)"
& $az webapp deploy -n $App -g $Rg --src-path $zip --type zip --async true -o none
Remove-Item $zip
step "polling build/deploy status"
$tok = & $az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv
$h = @{ Authorization = "Bearer $tok" }
$final = $null
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep 15
  try { $d = Invoke-RestMethod -Headers $h -Uri "https://$App.scm.azurewebsites.net/api/deployments/latest" -TimeoutSec 30 } catch { continue }
  "  status=$($d.status) complete=$($d.complete) $($d.progress)"
  if ($d.complete) { $final = $d; break }
}
if ($final -and $final.status -eq 4) { "deployment succeeded (id $($final.id))" }
elseif ($final) { "DEPLOYMENT FAILED (status $($final.status)): see https://$App.scm.azurewebsites.net/api/deployments/$($final.id)/log" }
else { "still running after 15 minutes; check https://$App.scm.azurewebsites.net/api/deployments/latest" }
"site: https://$App.azurewebsites.net"
step "DEPLOY_CODE_DONE"
