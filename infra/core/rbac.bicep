@description('System-assigned managed identity of the Function App.')
param functionPrincipalId string

param keyVaultName string
param lakeAccountName string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource lake 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: lakeAccountName
}

// Key Vault Secrets User — read the GitHub credential only.
resource keyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionPrincipalId, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: functionPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Blob Data Contributor — write raw JSON partitions into the lake.
resource lakeBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(lake.id, functionPrincipalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  scope: lake
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: functionPrincipalId
    principalType: 'ServicePrincipal'
  }
}
