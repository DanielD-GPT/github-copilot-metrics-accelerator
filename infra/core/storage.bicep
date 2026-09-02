param location string
param tags object
param name string
param filesystemName string = 'raw'

@description('Days to retain raw payloads. They contain per-developer personal data, so this is bounded by default.')
param rawRetentionDays int = 730

// Hierarchical namespace makes this ADLS Gen2 rather than flat blob storage,
// which is what gives us real directory semantics for dt=YYYY-MM-DD partitions.
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 30
    }
  }
}

resource rawFilesystem 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: filesystemName
}

// The raw zone is the replay source for the warehouse: cool after 90 days,
// archive after a year, never auto-delete.
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-01-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'tier-raw-zone'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${filesystemName}/']
            }
            actions: {
              baseBlob: {
                tierToCool: {
                  daysAfterModificationGreaterThan: 90
                }
                tierToArchive: {
                  daysAfterModificationGreaterThan: 365
                }
                delete: {
                  daysAfterModificationGreaterThan: rawRetentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
}

output name string = storage.name
output filesystemName string = rawFilesystem.name
output dfsEndpoint string = storage.properties.primaryEndpoints.dfs
