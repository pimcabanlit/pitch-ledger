// Pitch Ledger on Azure Container Apps: the app platform around the data services.
//
// Creates: a Log Analytics workspace (daily cap), a Container Apps environment
// (consumption, scale to zero), one user-assigned managed identity for every app and job,
// its data-plane roles on the existing data services and Foundry, the API and Studio apps,
// and three manually started jobs (live service, Match Lab feed, replay).
//
// The data services (Event Hubs, Service Bus, Cosmos DB, Web PubSub) and Foundry were
// created by hand (docs/azure-setup.md) and are referenced, never changed. Set
// createDataServices=true only for a fresh environment: modules/data-services.bicep then
// creates them with the same settings. The Azure Data Explorer free cluster is not an
// Azure resource; give the identity its database roles with the KQL in docs/azure-setup.md.
//
// No secrets: images are public on GHCR, and every service is reached with the managed
// identity (Microsoft Entra ID).
//
// Deploy (GitHub Actions does this after CI passes on main):
//   az deployment group create -g <resource-group> -f infra/main.bicep \
//     -p infra/main.bicepparam -p imageTag=<git sha> adxClusterUri=<uri>

targetScope = 'resourceGroup'

@description('Region for the new resources.')
param location string = resourceGroup().location

@description('Image tag to run (the git commit SHA from the deploy workflow).')
param imageTag string

@description('Backend image (API, live service, feed, replay) without a tag.')
param backendImage string = 'ghcr.io/pimcabanlit/pitch-ledger'

@description('Studio image without a tag.')
param studioImage string = 'ghcr.io/pimcabanlit/pitch-ledger-studio'

@description('Resource group of Event Hubs, Service Bus, Cosmos DB and Web PubSub (empty: this one).')
param dataResourceGroup string = ''

@description('Resource group of the Foundry resource (empty: this one).')
param foundryResourceGroup string = ''

@description('Create the data services (a fresh environment only; leave false otherwise).')
param createDataServices bool = false

param eventHubsNamespace string = 'pitch-ledger-feed'
param eventHubName string = 'match-events'
param serviceBusNamespace string = 'pitch-ledger-bus'
param serviceBusQueue string = 'triggers'
param cosmosAccount string = 'pitch-ledger-cosmos'
param cosmosDatabase string = 'pitchledger'
param cosmosContainer string = 'ledger'
param webPubSubName string = 'pitch-ledger-push'

@description('Foundry resource (AI Services account) whose models the agents call.')
param foundryAccount string = 'inside-the-game'

@description('Foundry project endpoint.')
param foundryProjectEndpoint string

param modelStrong string = 'gpt-4.1-mini'
param modelSmall string = 'gpt-4.1-mini'

@description('Model calls allowed per run (cost cap).')
param modelCallBudget int = 120

@description('Azure Data Explorer free cluster URI; empty: the jobs write the event store as files.')
param adxClusterUri string = ''
param adxDatabase string = 'matchdata'

@description('API app registration for producer sign-in; empty: producer endpoints refuse (fail closed).')
param apiAudience string = ''

@description('Minimum replicas of the API and Studio (0 = scale to zero, inside the free grant).')
@minValue(0)
param minReplicas int = 0

@description('Arguments of the live-service job.')
param liveArgs array = ['--approve', '--inject-error', '--fresh', '--idle', '120']

@description('Arguments of the feed job (a match from the image and a pace).')
param feedArgs array = ['data/M0002', '--speed', '25']

@description('Arguments of the replay job.')
param replayArgs array = ['data/M0002', '--approve', '--inject-error']

param tags object = {
  project: 'pitch-ledger'
}

var prefix = 'pitch-ledger'
var dataGroup = empty(dataResourceGroup) ? resourceGroup().name : dataResourceGroup
var foundryGroup = empty(foundryResourceGroup) ? resourceGroup().name : foundryResourceGroup
var apiName = '${prefix}-api' // the Studio image forwards /api/v1 to http://pitch-ledger-api

// Data services ---------------------------------------------------------------------

module dataServices 'modules/data-services.bicep' = if (createDataServices) {
  name: 'data-services'
  scope: resourceGroup(dataGroup)
  params: {
    location: location
    eventHubsNamespace: eventHubsNamespace
    eventHubName: eventHubName
    serviceBusNamespace: serviceBusNamespace
    serviceBusQueue: serviceBusQueue
    cosmosAccount: cosmosAccount
    cosmosDatabase: cosmosDatabase
    cosmosContainer: cosmosContainer
    webPubSubName: webPubSubName
    tags: tags
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccount
  scope: resourceGroup(dataGroup)
}

resource webPubSub 'Microsoft.SignalRService/webPubSub@2024-03-01' existing = {
  name: webPubSubName
  scope: resourceGroup(dataGroup)
}

// Identity and roles ----------------------------------------------------------------

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-apps'
  location: location
  tags: tags
}

module dataRoles 'modules/data-roles.bicep' = {
  name: 'data-roles'
  scope: resourceGroup(dataGroup)
  params: {
    principalId: identity.properties.principalId
    eventHubsNamespace: eventHubsNamespace
    serviceBusNamespace: serviceBusNamespace
    cosmosAccount: cosmosAccount
    webPubSubName: webPubSubName
  }
  dependsOn: [dataServices]
}

module foundryRole 'modules/foundry-role.bicep' = {
  name: 'foundry-role'
  scope: resourceGroup(foundryGroup)
  params: {
    principalId: identity.properties.principalId
    foundryAccount: foundryAccount
  }
}

// Logs and environment --------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.1') } // cost guard: stops ingesting after 0.1 GB a day
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

// Settings every backend container reads (names from the settings classes in src/).
var backendEnv = concat(
  [
    { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // managed identity sign-in
    { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
    { name: 'PITCH_LEDGER_FEED', value: 'eventhubs' }
    { name: 'PITCH_LEDGER_EVENTHUB_NAMESPACE', value: '${eventHubsNamespace}.servicebus.windows.net' }
    { name: 'PITCH_LEDGER_EVENTHUB_NAME', value: eventHubName }
    { name: 'PITCH_LEDGER_LEDGER', value: 'cosmos' }
    { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
    { name: 'PITCH_LEDGER_COSMOS_DATABASE', value: cosmosDatabase }
    { name: 'PITCH_LEDGER_COSMOS_CONTAINER', value: cosmosContainer }
    { name: 'PITCH_LEDGER_QUEUE', value: 'servicebus' }
    { name: 'PITCH_LEDGER_SERVICEBUS_NAMESPACE', value: '${serviceBusNamespace}.servicebus.windows.net' }
    { name: 'PITCH_LEDGER_SERVICEBUS_QUEUE', value: serviceBusQueue }
    { name: 'PITCH_LEDGER_PUSH', value: 'webpubsub' }
    { name: 'PITCH_LEDGER_WEBPUBSUB_ENDPOINT', value: 'https://${webPubSub.properties.hostName}' }
    { name: 'PITCH_LEDGER_MODEL_PROVIDER', value: 'foundry' }
    { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
    { name: 'PITCH_LEDGER_MODEL_STRONG', value: modelStrong }
    { name: 'PITCH_LEDGER_MODEL_SMALL', value: modelSmall }
    { name: 'PITCH_LEDGER_MODEL_CALL_BUDGET', value: string(modelCallBudget) }
    { name: 'PITCH_LEDGER_STORE', value: empty(adxClusterUri) ? 'file' : 'adx' }
  ],
  empty(adxClusterUri)
    ? []
    : [
        { name: 'ADX_CLUSTER_URI', value: adxClusterUri }
        { name: 'ADX_DATABASE', value: adxDatabase }
      ]
)

var identityConfig = {
  type: 'UserAssigned'
  userAssignedIdentities: { '${identity.id}': {} }
}

// Apps --------------------------------------------------------------------------------

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiName
  location: location
  tags: tags
  identity: identityConfig
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false // reached only by the Studio, inside the environment
        targetPort: 8000
        allowInsecure: true // plain HTTP by app name within the environment
      }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${backendImage}:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(backendEnv, [
            { name: 'PITCH_LEDGER_API_AUTH', value: 'entra' }
            { name: 'PITCH_LEDGER_API_AUDIENCE', value: apiAudience }
          ])
          probes: [
            {
              type: 'Readiness'
              httpGet: { path: '/api/v1/health', port: 8000 }
              initialDelaySeconds: 3
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1 // one replica: the decision rate limit is per process
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '30' } } }]
      }
    }
  }
  dependsOn: [dataRoles, foundryRole]
}

resource studio 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-studio'
  location: location
  tags: tags
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 3000
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'studio'
          image: '${studioImage}:${imageTag}'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: 2
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '50' } } }]
      }
    }
  }
  dependsOn: [api]
}

// Jobs (started by hand: portal "Run now" or `az containerapp job start`) --------------

var jobs = [
  {
    name: '${prefix}-live'
    command: ['pitch-ledger', 'live']
    args: concat(liveArgs, empty(adxClusterUri) ? [] : ['--store', 'adx']) // feed, queue, ledger: settings
    cpu: '1.0'
    memory: '2Gi'
  }
  {
    name: '${prefix}-feed'
    command: ['pitch-ledger', 'stream']
    args: feedArgs
    cpu: '0.5'
    memory: '1Gi'
  }
  {
    name: '${prefix}-replay'
    command: ['pitch-ledger', 'replay']
    args: replayArgs
    cpu: '1.0'
    memory: '2Gi'
  }
]

resource job 'Microsoft.App/jobs@2024-03-01' = [
  for j in jobs: {
    name: j.name
    location: location
    tags: tags
    identity: identityConfig
    properties: {
      environmentId: environment.id
      workloadProfileName: 'Consumption'
      configuration: {
        triggerType: 'Manual'
        replicaTimeout: 3600
        replicaRetryLimit: 0
        manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      }
      template: {
        containers: [
          {
            name: 'main'
            image: '${backendImage}:${imageTag}'
            command: j.command
            args: j.args
            resources: { cpu: json(j.cpu), memory: j.memory }
            env: backendEnv
          }
        ]
      }
    }
    dependsOn: [dataRoles, foundryRole]
  }
]

output studioUrl string = 'https://${studio.properties.configuration.ingress.fqdn}'
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
output environmentName string = environment.name
