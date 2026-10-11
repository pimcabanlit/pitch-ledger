// Season history: the Cosmos DB container for match records (one document per match, one
// partition per season), in the existing ledger database. No throughput of its own: it
// shares the database's free 1000 RU/s, so it adds no cost. Safe to deploy again.

param cosmosAccount string
param cosmosDatabase string
param containerName string

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccount
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' existing = {
  parent: account
  name: cosmosDatabase
}

resource history 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: { paths: ['/season'], kind: 'Hash' }
    }
  }
}
