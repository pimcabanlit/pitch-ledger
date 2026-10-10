#!/usr/bin/env bash
# Create the apps' managed identity and give it its data-plane roles (run once, by the
# subscription owner, in Azure Cloud Shell: Bash).
#
# Why a script: on this subscription the owner's own Owner role carries a condition that
# forbids delegating role assignment, so the deploy identity cannot grant roles; the owner
# grants these ordinary data roles once instead (infra/main.bicep: assignRoles=false).
#
# Usage: paste into Cloud Shell, or `bash infra/grant-roles.sh <resource-group>`.
# Safe to re-run: existing identity and role assignments are kept.
set -euo pipefail

RG="${1:-inside-the-game}"          # where the Container Apps resources go
IDENTITY="pitch-ledger-apps"

az identity create --resource-group "$RG" --name "$IDENTITY" --output none
PRINCIPAL=$(az identity show -g "$RG" -n "$IDENTITY" --query principalId -o tsv)

resource_id() {  # name, type -> full resource ID (found in any resource group)
  az resource list --name "$1" --resource-type "$2" --query "[0].id" -o tsv
}

grant() {  # role ID, resource name, resource type, label
  local scope
  scope=$(resource_id "$2" "$3")
  if [ -z "$scope" ]; then echo "MISSING  $2 ($3) not found"; return 1; fi
  az role assignment create --assignee-object-id "$PRINCIPAL" \
    --assignee-principal-type ServicePrincipal --role "$1" --scope "$scope" --output none
  echo "OK       $4 on $2"
}

grant 2b629674-e913-4c01-ae53-ef4638d8f975 pitch-ledger-feed Microsoft.EventHub/namespaces "Event Hubs Data Sender"
grant a638d3c7-ab3a-418d-83e6-5f17a39d4fde pitch-ledger-feed Microsoft.EventHub/namespaces "Event Hubs Data Receiver"
grant 69a216fc-b8fb-44d8-bc22-1f3c2cd27a39 pitch-ledger-bus Microsoft.ServiceBus/namespaces "Service Bus Data Sender"
grant 4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0 pitch-ledger-bus Microsoft.ServiceBus/namespaces "Service Bus Data Receiver"
grant 12cf5a90-567b-43ae-8102-96cf46c7d9b4 pitch-ledger-push Microsoft.SignalRService/WebPubSub "Web PubSub Service Owner"
grant 53ca6127-db72-4b80-b1b0-d745d6d5456d inside-the-game Microsoft.CognitiveServices/accounts "Foundry User"

# Cosmos DB data access is a Cosmos role (not Azure RBAC): Built-in Data Contributor.
COSMOS_RG=$(az resource list --name pitch-ledger-cosmos \
  --resource-type Microsoft.DocumentDB/databaseAccounts --query "[0].resourceGroup" -o tsv)
az cosmosdb sql role assignment create --account-name pitch-ledger-cosmos \
  --resource-group "$COSMOS_RG" --scope "/" --principal-id "$PRINCIPAL" \
  --role-definition-id 00000000-0000-0000-0000-000000000002 --output none
echo "OK       Cosmos DB Built-in Data Contributor on pitch-ledger-cosmos"

echo
echo "Resource groups of the services (for the deploy variables):"
az resource list --query "[?starts_with(name, 'pitch-ledger') || name == 'inside-the-game'].{name: name, group: resourceGroup}" -o table
echo
echo "Apps identity client ID (for the ADX roles later): $(az identity show -g "$RG" -n "$IDENTITY" --query clientId -o tsv)"
