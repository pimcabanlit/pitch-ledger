// The data services, as created by hand for this project (docs/azure-setup.md), for
// recreating them in a fresh environment. main.bicep deploys this module only with
// createDataServices=true; the existing resources are never redeployed from it.
//
// Costs: Event Hubs Basic has a monthly base charge (about US$11); Service Bus Basic is
// per operation; Cosmos DB uses the free tier (one free-tier account per subscription);
// Web PubSub Free has no charge (one Free unit per subscription).

param location string
param eventHubsNamespace string
param eventHubName string
param serviceBusNamespace string
param serviceBusQueue string
param cosmosAccount string
param cosmosDatabase string
param cosmosContainer string
param webPubSubName string
param tags object

resource eventHubs 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: eventHubsNamespace
  location: location
  tags: tags
  sku: { name: 'Basic', tier: 'Basic', capacity: 1 }
  properties: { minimumTlsVersion: '1.2' }
}

resource hub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubs
  name: eventHubName
  properties: {
    partitionCount: 2
    messageRetentionInDays: 1
  }
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusNamespace
  location: location
  tags: tags
  sku: { name: 'Basic', tier: 'Basic' }
  properties: { minimumTlsVersion: '1.2' }
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: serviceBusQueue
  properties: {
    maxDeliveryCount: 3
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: true
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosAccount
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: true
    capacity: { totalThroughputLimit: 1000 } // the free amount: never billed for throughput
    locations: [{ locationName: location, failoverPriority: 0 }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    minimalTlsVersion: 'Tls12'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: cosmosDatabase
  properties: {
    resource: { id: cosmosDatabase }
    options: { throughput: 1000 }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: cosmosContainer
  properties: {
    resource: {
      id: cosmosContainer
      partitionKey: { paths: ['/match_id'], kind: 'Hash' }
    }
  }
}

resource webPubSub 'Microsoft.SignalRService/webPubSub@2024-03-01' = {
  name: webPubSubName
  location: location
  tags: tags
  sku: { name: 'Free_F1', tier: 'Free', capacity: 1 }
  properties: {}
}
