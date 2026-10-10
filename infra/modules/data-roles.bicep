// Data-plane roles for the apps' managed identity on the data services (no keys).

param principalId string
param eventHubsNamespace string
param serviceBusNamespace string
param cosmosAccount string
param webPubSubName string

// Built-in role definition IDs (docs: Azure built-in roles).
var roles = {
  eventHubsDataSender: '2b629674-e913-4c01-ae53-ef4638d8f975'
  eventHubsDataReceiver: 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde'
  serviceBusDataSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  serviceBusDataReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
  webPubSubServiceOwner: '12cf5a90-567b-43ae-8102-96cf46c7d9b4'
}
var cosmosDataContributor = '00000000-0000-0000-0000-000000000002' // Cosmos DB Built-in Data Contributor

resource eventHubs 'Microsoft.EventHub/namespaces@2024-01-01' existing = {
  name: eventHubsNamespace
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespace
}

resource webPubSub 'Microsoft.SignalRService/webPubSub@2024-03-01' existing = {
  name: webPubSubName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccount
}

resource eventHubsSend 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(eventHubs.id, principalId, roles.eventHubsDataSender)
  scope: eventHubs
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.eventHubsDataSender)
  }
}

resource eventHubsReceive 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(eventHubs.id, principalId, roles.eventHubsDataReceiver)
  scope: eventHubs
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.eventHubsDataReceiver)
  }
}

resource serviceBusSend 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, principalId, roles.serviceBusDataSender)
  scope: serviceBus
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataSender)
  }
}

resource serviceBusReceive 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, principalId, roles.serviceBusDataReceiver)
  scope: serviceBus
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataReceiver)
  }
}

resource webPubSubOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(webPubSub.id, principalId, roles.webPubSubServiceOwner)
  scope: webPubSub
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.webPubSubServiceOwner)
  }
}

resource cosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, principalId, cosmosDataContributor)
  properties: {
    principalId: principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    scope: cosmos.id
  }
}
