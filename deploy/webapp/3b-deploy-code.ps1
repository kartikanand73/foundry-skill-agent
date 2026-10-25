# Step 3b: (re)deploy only the Streamlit code to the existing web app.
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
step "zip webapp/ (Python zipfile: Unix permissions + forward slashes)"
$src = Resolve-Path "$PSScriptRoot\..\..\webapp"
$zip = Join-Path $env:TEMP "webapp-$(Get-Date -Format yyyyMMddHHmmss).zip"
$py = Resolve-Path "$PSScriptRoot\..\..\.venv\Scripts\python.exe" -ErrorAction SilentlyContinue
if (-not $py) { $py = "python" }
& $py "$PSScriptRoot\make_zip.py" $src $zip
step "zip deploy"
& $az webapp deploy -n $App -g $Rg --src-path $zip --type zip -o none
Remove-Item $zip
"site: https://$App.azurewebsites.net"
step "DEPLOY_CODE_DONE"
