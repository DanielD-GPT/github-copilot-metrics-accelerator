param location string
param tags object
param planName string
param functionAppName string
param deploymentStorageName string
param applicationInsightsConnectionString string
param keyVaultName string
param lakeAccountName string
param lakeFilesystemName string
param sqlServerFqdn string
param sqlDatabaseName string
param githubEnterprise string
param githubOrgs string
param ingestionSchedule string

@description('Name of the Key Vault secret holding the GitHub PAT or GitHub App private key.')
param githubCredentialSecretName string = 'github-credential'

@description('Name of the Key Vault secret holding the Teams outgoing webhook shared secret.')
param teamsWebhookSecretName string = 'teams-webhook-secret'

var deploymentContainerName = 'deploymentpackage'

resource deploymentStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: deploymentStorageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: deploymentStorage
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: deploymentContainerName
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'ingest' })
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${deploymentStorage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: deploymentStorage.properties.primaryEndpoints.blob
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: deploymentStorage.properties.primaryEndpoints.queue
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: deploymentStorage.properties.primaryEndpoints.table
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: applicationInsightsConnectionString
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'KEY_VAULT_NAME'
          value: keyVaultName
        }
        {
          name: 'GITHUB_CREDENTIAL_SECRET_NAME'
          value: githubCredentialSecretName
        }
        {
          name: 'TEAMS_WEBHOOK_SECRET_NAME'
          value: teamsWebhookSecretName
        }
        {
          name: 'GITHUB_ENTERPRISE'
          value: githubEnterprise
        }
        {
          name: 'GITHUB_ORGS'
          value: githubOrgs
        }
        {
          name: 'LAKE_ACCOUNT_NAME'
          value: lakeAccountName
        }
        {
          name: 'LAKE_FILESYSTEM_NAME'
          value: lakeFilesystemName
        }
        {
          name: 'SQL_CONNECTION_STRING'
          value: 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:${sqlServerFqdn},1433;Database=${sqlDatabaseName};Authentication=ActiveDirectoryMsi;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;'
        }
        {
          name: 'INGESTION_SCHEDULE'
          value: ingestionSchedule
        }
        {
          name: 'BACKFILL_DAYS'
          value: '28'
        }
        {
          name: 'RELOAD_TRAILING_DAYS'
          value: '7'
        }
        {
          name: 'METRICS_SCOPE'
          value: 'organization'
        }
        {
          name: 'MAX_BILLING_USERS'
          value: '2000'
        }
        {
          name: 'SQL_FAST_EXECUTEMANY'
          value: 'true'
        }
        {
          name: 'FAIL_ON_EMPTY_REPORT'
          value: 'true'
        }
      ]
    }
  }
}

// Flex Consumption needs Storage Blob Data Owner on its own deployment container.
resource deploymentStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(deploymentStorage.id, functionApp.id, 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  scope: deploymentStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output name string = functionApp.name
output uri string = 'https://${functionApp.properties.defaultHostName}'
output principalId string = functionApp.identity.principalId
