# Step 12: fulfillment-risk screen + skill. The snapshot lives in the fulfillment-risk-platform project
# (PostgreSQL psql-fr-ka2026, centralus). Its provision.py step 10 already made the web app identity and the hosted
# agent identity PostgreSQL principals with role fr_reader (SELECT on fr.v_* views only).
# This step: the web app setting + the Entra role for you (and optionally another user).
param([string]$AlsoUpn = '')
. "$PSScriptRoot\config.ps1"
$ErrorActionPreference = 'Continue'
$fr = 'host=psql-fr-ka2026.postgres.database.azure.com dbname=fr user=skillagent-chat-ka2026'
step "web app setting FR_DATABASE_URL (no secret: Entra token auth)"
& $az webapp config appsettings set -n $App -g $Rg --settings "FR_DATABASE_URL=$fr" -o none
step "role SkillAgent.FulfillmentRisk"
& "$PSScriptRoot\8-user-roles.ps1" -OnlyRoles 'SkillAgent.FulfillmentRisk'
if ($AlsoUpn) { & "$PSScriptRoot\8-user-roles.ps1" -Upn $AlsoUpn -OnlyRoles 'SkillAgent.FulfillmentRisk' }
step "FULFILLMENT_DONE (sign out and back in to pick up the role)"
