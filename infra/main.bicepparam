// Parameters for infra/main.bicep. No secrets here: names, endpoints and model deployments.
// The deploy workflow sets IMAGE_TAG (the commit SHA) and, from repository variables,
// ADX_CLUSTER_URI, API_AUDIENCE, DATA_RESOURCE_GROUP and FOUNDRY_RESOURCE_GROUP (all optional).
using 'main.bicep'

param imageTag = readEnvironmentVariable('IMAGE_TAG', 'latest')
param adxClusterUri = readEnvironmentVariable('ADX_CLUSTER_URI', '')
param apiAudience = readEnvironmentVariable('API_AUDIENCE', '')
param dataResourceGroup = readEnvironmentVariable('DATA_RESOURCE_GROUP', '')
param foundryResourceGroup = readEnvironmentVariable('FOUNDRY_RESOURCE_GROUP', '')
param foundryProjectEndpoint = 'https://inside-the-game.services.ai.azure.com/api/projects/inside-the-game'
param modelStrong = 'gpt-4.1-mini'
param modelSmall = 'gpt-4.1-mini'
param minReplicas = 0
