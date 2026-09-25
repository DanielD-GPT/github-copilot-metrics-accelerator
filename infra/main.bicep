targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment; used to derive resource names.')
param environmentName string

@minLength(1)
@description('Azure region for all resources.')
param location string

@description('Fabric workspace ID containing the Warehouse. The workspace must use Fabric capacity.')
param fabricWorkspaceId string

@description('Fabric Warehouse SQL endpoint, for example abc.datawarehouse.fabric.microsoft.com.')
param fabricSqlEndpoint string

@description('Display name of the Fabric Warehouse.')
param fabricWarehouseName string = 'copilotmetrics'

@description('Object ID of the deployment principal that receives Key Vault administration access.')
param principalId string

@description('Principal type of the Key Vault administrator.')
@allowed(['User', 'Group', 'Application'])
param principalType string = 'User'

@description('GitHub enterprise slug. Optional; metrics and billing are pulled per organization.')
param githubEnterprise string

@description('Comma-separated GitHub org slugs used by the Copilot seats API.')
param githubOrgs string

@description('GitHub App ID. Leave empty to authenticate with a PAT instead.')
param githubAppId string = ''

@description('GitHub App installation ID. Required when githubAppId is set.')
param githubAppInstallationId string = ''

@description('Daily ingestion schedule as an NCRONTAB expression (UTC).')
param ingestionSchedule string = '0 0 2 * * *'

@description('Email address for ingestion failure alerts. Leave empty to skip alerting.')
param alertEmail string = ''

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  solution: 'github-copilot-metrics-accelerator'
}

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

module monitoring './core/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    logAnalyticsName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    applicationInsightsName: '${abbrs.insightsComponents}${resourceToken}'
    alertEmail: alertEmail
  }
}

module keyVault './core/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    tags: tags
    name: '${abbrs.keyVaultVaults}${resourceToken}'
    adminPrincipalId: principalId
    adminPrincipalType: principalType
  }
}

module lake './core/storage.bicep' = {
  name: 'lake'
  scope: rg
  params: {
    location: location
    tags: tags
    name: '${abbrs.storageStorageAccounts}lake${resourceToken}'
  }
}

module functionApp './core/function.bicep' = {
  name: 'function'
  scope: rg
  params: {
    location: location
    tags: tags
    planName: '${abbrs.webServerFarms}${resourceToken}'
    functionAppName: '${abbrs.webSitesFunctions}${resourceToken}'
    deploymentStorageName: '${abbrs.storageStorageAccounts}fn${resourceToken}'
    applicationInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
    keyVaultName: keyVault.outputs.name
    lakeAccountName: lake.outputs.name
    lakeFilesystemName: lake.outputs.filesystemName
    fabricWorkspaceId: fabricWorkspaceId
    fabricSqlEndpoint: fabricSqlEndpoint
    fabricWarehouseName: fabricWarehouseName
    githubEnterprise: githubEnterprise
    githubOrgs: githubOrgs
    githubAppId: githubAppId
    githubAppInstallationId: githubAppInstallationId
    ingestionSchedule: ingestionSchedule
  }
}

module functionRbac './core/rbac.bicep' = {
  name: 'function-rbac'
  scope: rg
  params: {
    functionPrincipalId: functionApp.outputs.principalId
    keyVaultName: keyVault.outputs.name
    lakeAccountName: lake.outputs.name
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_KEY_VAULT_NAME string = keyVault.outputs.name
output AZURE_KEY_VAULT_ENDPOINT string = keyVault.outputs.endpoint
output LAKE_ACCOUNT_NAME string = lake.outputs.name
output LAKE_FILESYSTEM_NAME string = lake.outputs.filesystemName
output FABRIC_WORKSPACE_ID string = fabricWorkspaceId
output FABRIC_SQL_ENDPOINT string = fabricSqlEndpoint
output FABRIC_WAREHOUSE_NAME string = fabricWarehouseName
output FUNCTION_APP_NAME string = functionApp.outputs.name
output FUNCTION_APP_URI string = functionApp.outputs.uri
output FUNCTION_PRINCIPAL_ID string = functionApp.outputs.principalId
