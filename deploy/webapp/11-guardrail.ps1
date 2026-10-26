# Step 11: Foundry guardrail (content filter / RAI policy) for the gpt-5 deployment the agent calls.
# The hosted agent calls gpt-5 directly, so the MODEL DEPLOYMENT's guardrail screens every router and
# skill call - including tool results, which go back to the model as input.
#   input  (Prompt)    : hate / sexual / violence / self-harm block at Medium; jailbreak + indirect attack block
#   output (Completion): same four categories at Medium; protected material text block, code annotate
# Rerun-safe. Revert: set raiPolicyName back to Microsoft.DefaultV2 (see README 3g).
param([string]$Policy = 'skill-agent-guardrail', [string]$Deployment = 'gpt-5', [string]$Threshold = 'Medium')
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$acct = "$Arm/Microsoft.CognitiveServices/accounts/$FoundryAccount"
$api = 'api-version=2025-06-01'
function Put-Json($url, $obj) {
  $f = Join-Path $env:TEMP ("arm-" + [guid]::NewGuid() + ".json")
  [IO.File]::WriteAllText($f, ($obj | ConvertTo-Json -Depth 10))
  & $az rest --method put --url $url --body "@$f" --query "{name:name, state:properties.provisioningState}" -o json
  Remove-Item $f
}

step "guardrail $Policy"
$filters = @()
foreach ($src in 'Prompt', 'Completion') {
  foreach ($cat in 'Hate', 'Sexual', 'Violence', 'Selfharm') {
    $filters += @{ name = $cat; source = $src; severityThreshold = $Threshold; blocking = $true; enabled = $true }
  }
}
$filters += @{ name = 'Jailbreak'; source = 'Prompt'; blocking = $true; enabled = $true }
$filters += @{ name = 'Indirect Attack'; source = 'Prompt'; blocking = $true; enabled = $true }
$filters += @{ name = 'Protected Material Text'; source = 'Completion'; blocking = $true; enabled = $true }
$filters += @{ name = 'Protected Material Code'; source = 'Completion'; blocking = $false; enabled = $true }
Put-Json "$acct/raiPolicies/$Policy`?$api" @{ properties = @{ basePolicyName = 'Microsoft.DefaultV2'; mode = 'Blocking'; contentFilters = $filters } }

step "assign it to deployment $Deployment (model, sku and capacity unchanged)"
$d = (& $az rest --method get --url "$acct/deployments/$Deployment`?$api" -o json) -join "`n" | ConvertFrom-Json
$body = @{
  sku = @{ name = $d.sku.name; capacity = $d.sku.capacity }
  properties = @{
    model = @{ format = $d.properties.model.format; name = $d.properties.model.name; version = $d.properties.model.version }
    raiPolicyName = $Policy
  }
}
if ($d.properties.versionUpgradeOption) { $body.properties.versionUpgradeOption = $d.properties.versionUpgradeOption }
Put-Json "$acct/deployments/$Deployment`?$api" $body
& $az rest --method get --url "$acct/deployments/$Deployment`?$api" --query "{rai:properties.raiPolicyName, state:properties.provisioningState, capacity:sku.capacity}" -o json
step "GUARDRAIL_DONE"
