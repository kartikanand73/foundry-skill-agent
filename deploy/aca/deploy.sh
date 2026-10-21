#!/usr/bin/env bash
# Fallback: run the SAME image on Azure Container Apps (scale-to-zero, consumption plan).
# Use only if Foundry Hosted Agents is blocked for your subscription/region.
#
# Required env vars:
#   SUBSCRIPTION_ID   target subscription (checked explicitly below)
#   LOCATION          e.g. eastus2
#   RG                resource group to create/use, e.g. rg-skill-agent
#   AOAI_RESOURCE_ID  ARM id of your existing Foundry / Azure OpenAI account
#   AOAI_ENDPOINT     https://<account>.services.ai.azure.com
#   MODEL_DEPLOYMENT_NAME
set -euo pipefail
: "${SUBSCRIPTION_ID:?}" "${LOCATION:?}" "${RG:?}" "${AOAI_RESOURCE_ID:?}" "${AOAI_ENDPOINT:?}" "${MODEL_DEPLOYMENT_NAME:?}"

APP=${APP:-skill-agent}
ACR=${ACR:-acrskillagent$RANDOM}
ENV_NAME=${ENV_NAME:-cae-skill-agent}
cd "$(dirname "$0")/../.."

az account set --subscription "$SUBSCRIPTION_ID"
echo "Target: $(az account show --query '{name:name,id:id}' -o tsv)  region=$LOCATION  rg=$RG"
read -r -p "Proceed with provisioning? [y/N] " ok; [[ "$ok" == "y" ]] || exit 1

az group create -n "$RG" -l "$LOCATION" -o none
az acr create -g "$RG" -n "$ACR" --sku Basic -o none
az acr build -r "$ACR" -t skill-agent:v1 .                     # cloud build, no local Docker needed
az containerapp env create -g "$RG" -n "$ENV_NAME" -l "$LOCATION" -o none

az containerapp create -g "$RG" -n "$APP" --environment "$ENV_NAME" \
  --image "$ACR.azurecr.io/skill-agent:v1" --registry-server "$ACR.azurecr.io" --registry-identity system \
  --system-assigned --target-port 8088 --ingress external \
  --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi \
  --env-vars AZURE_OPENAI_ENDPOINT="$AOAI_ENDPOINT" MODEL_DEPLOYMENT_NAME="$MODEL_DEPLOYMENT_NAME" \
             AZURE_TOKEN_SCOPE="https://cognitiveservices.azure.com/.default" -o none

PRINCIPAL=$(az containerapp show -g "$RG" -n "$APP" --query identity.principalId -o tsv)
az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" --scope "$AOAI_RESOURCE_ID" -o none

FQDN=$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)
echo
echo "Deployed. Role assignment can take a few minutes to apply. Test with:"
echo "  curl -X POST \"https://$FQDN/invocations?agent_session_id=t1\" -H 'Content-Type: application/json' -d '{\"message\":\"What is our vacation policy?\"}'"
echo "WARNING: ingress is public with no auth. Enable Container Apps authentication or use --ingress internal for anything beyond a demo."
echo "Delete everything:  az group delete -n $RG --yes --no-wait"
