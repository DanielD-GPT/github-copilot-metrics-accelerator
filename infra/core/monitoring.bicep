param location string
param tags object
param logAnalyticsName string
param applicationInsightsName string

@description('Email address for ingestion failure alerts. Leave empty to skip alerting.')
param alertEmail string = ''

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(alertEmail)) {
  name: 'ag-copilot-metrics'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'cpltmetric'
    enabled: true
    emailReceivers: [
      {
        name: 'operator'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// Two-day window rather than one: the timer runs daily, so a 24h window would fire
// on a single transient failure.
resource ingestionStalled 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-copilot-ingestion-stalled'
  location: location
  tags: tags
  properties: {
    displayName: 'Copilot metrics ingestion stalled'
    description: 'No successful ingestion run recorded in the last 48 hours.'
    severity: 2
    enabled: true
    scopes: [applicationInsights.id]
    evaluationFrequency: 'PT6H'
    windowSize: 'P2D'
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: 'requests | where name == \'ingest_daily\' and success == true'
          timeAggregation: 'Count'
          operator: 'LessThan'
          threshold: 1
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: empty(alertEmail) ? [] : [actionGroup.id]
    }
  }
}

output logAnalyticsId string = logAnalytics.id
output applicationInsightsName string = applicationInsights.name
output applicationInsightsConnectionString string = applicationInsights.properties.ConnectionString
