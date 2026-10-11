#!/usr/bin/env bash
# Enterprise controls on the live resources (run once, by the subscription owner, in Azure
# Cloud Shell: Bash, after the Deploy workflow has created Application Insights).
#
# 1. Tracing: Application Insights refuses key-based ingestion, so give the apps' managed
#    identity (and the local service principal, for `pitch-ledger check-tracing`) the
#    Monitoring Metrics Publisher role on it.
# 2. Key auth off: Foundry, Cosmos DB, Event Hubs, Service Bus and Web PubSub then accept
#    only Microsoft Entra ID. Every Pitch Ledger component already signs in that way.
# 3. Least privilege: remove Azure AI Administrator from pitch-ledger-dev (calling models
#    needs only Foundry User, which it keeps).
#
# Why a script: on this subscription the owner's Owner role carries a condition that
# forbids delegating role assignment, so the deploy identity cannot grant roles
# (docs/azure-setup.md, lesson 15).
#
# Usage: paste into Cloud Shell, or `bash infra/enterprise-controls.sh <resource-group>`.
# Safe to re-run.
set -euo pipefail
RG="${1:-inside-the-game}"          # where the Container Apps resources go
IDENTITY="pitch-ledger-apps"
DEV_APP="pitch-ledger-dev"
METRICS_PUBLISHER=3913510d-42f4-4e42-8a64-420c390055eb

resource_id() {  # name, type -> full resource ID (found in any resource group)
  az resource list --name "$1" --resource-type "$2" --query "[0].id" -o tsv
}

echo "1. Tracing role (Monitoring Metrics Publisher on Application Insights)"
INSIGHTS=$(resource_id pitch-ledger-insights Microsoft.Insights/components)
if [ -z "$INSIGHTS" ]; then
  echo "MISSING  pitch-ledger-insights: run the Deploy workflow first"; exit 1
fi
APPS=$(az identity show -g "$RG" -n "$IDENTITY" --query principalId -o tsv)
az role assignment create --assignee-object-id "$APPS" --assignee-principal-type ServicePrincipal \
  --role "$METRICS_PUBLISHER" --scope "$INSIGHTS" --output none
echo "OK       $IDENTITY"
DEV=$(az ad sp list --display-name "$DEV_APP" --query "[0].id" -o tsv)
if [ -n "$DEV" ]; then
  az role assignment create --assignee-object-id "$DEV" --assignee-principal-type ServicePrincipal \
    --role "$METRICS_PUBLISHER" --scope "$INSIGHTS" --output none
  echo "OK       $DEV_APP"
fi

echo
echo "2. Key-based access off (Microsoft Entra ID only)"
disable_keys() {  # name, type
  local id
  id=$(resource_id "$1" "$2")
  if [ -z "$id" ]; then echo "MISSING  $1 ($2) not found"; return 0; fi
  az resource update --ids "$id" --set properties.disableLocalAuth=true --output none
  echo "OK       $1: disableLocalAuth=$(az resource show --ids "$id" --query properties.disableLocalAuth -o tsv)"
}
disable_keys inside-the-game Microsoft.CognitiveServices/accounts
disable_keys pitch-ledger-cosmos Microsoft.DocumentDB/databaseAccounts
disable_keys pitch-ledger-feed Microsoft.EventHub/namespaces
disable_keys pitch-ledger-bus Microsoft.ServiceBus/namespaces
disable_keys pitch-ledger-push Microsoft.SignalRService/WebPubSub

echo
echo "3. Least privilege: Azure AI Administrator off $DEV_APP"
if [ -n "$DEV" ]; then
  IDS=$(az role assignment list --assignee "$DEV" --all \
    --query "[?roleDefinitionName=='Azure AI Administrator'].id" -o tsv)
  if [ -n "$IDS" ]; then
    # shellcheck disable=SC2086
    az role assignment delete --ids $IDS --output none
    echo "OK       removed"
  else
    echo "OK       not assigned"
  fi
  echo "Roles $DEV_APP keeps:"
  az role assignment list --assignee "$DEV" --all --query "[].roleDefinitionName" -o tsv | sort -u | sed 's/^/         /'
fi

echo
echo "Application Insights connection string (where to send traces; not a credential, since"
echo "key-based ingestion is off). Put it in the cloud environment as"
echo "APPLICATIONINSIGHTS_CONNECTION_STRING to run 'pitch-ledger check-tracing' locally:"
az resource show --ids "$INSIGHTS" --query properties.ConnectionString -o tsv
