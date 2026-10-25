# Shared settings for the web app + APIM scripts. Non-secret.
$az  = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
$Sub = 'fe74279e-6ca8-48ff-bf54-90305b257322'
$Rg  = 'DB'; $Loc = 'eastus'
$FoundryAccount = 'db-accessHandOnsss3'
$ProtocolsUrl = 'https://db-accesshandonsss3.services.ai.azure.com/api/projects/proj-default/agents/skill-agent/endpoint/protocols'
$Kv   = 'kv-skillagent-ka2026'
$Apim = 'apim-skillagent-ka2026'
$Plan = 'asp-skillagent-ka2026'
$App  = 'skillagent-chat-ka2026'
$PublisherEmail = 'kartikanand@gmail.com'; $PublisherName = 'Kartik Anand'
$Arm = "https://management.azure.com/subscriptions/$Sub/resourceGroups/$Rg/providers"
function step($m){ "[$(Get-Date -Format HH:mm:ss)] $m" }
& $az account set --subscription $Sub
