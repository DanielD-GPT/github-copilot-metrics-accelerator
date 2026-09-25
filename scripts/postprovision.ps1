#!/usr/bin/env pwsh
# Grants the Function identity Fabric access, applies Warehouse objects, and reminds
# the operator to seed the GitHub credential.
# Invoked automatically by `azd up` via the postprovision hook.

$ErrorActionPreference = 'Stop'

$sqlServer   = $env:FABRIC_SQL_ENDPOINT
$sqlDatabase = $env:FABRIC_WAREHOUSE_NAME
$workspaceId = $env:FABRIC_WORKSPACE_ID
$keyVault    = $env:AZURE_KEY_VAULT_NAME
$functionApp = $env:FUNCTION_APP_NAME
$functionPrincipalId = $env:FUNCTION_PRINCIPAL_ID

if (-not $sqlServer) {
    throw 'FABRIC_SQL_ENDPOINT is required.'
}
if (-not $sqlDatabase) {
    throw 'FABRIC_WAREHOUSE_NAME is required.'
}
if (-not $workspaceId) {
    throw 'FABRIC_WORKSPACE_ID is required.'
}

if (-not (Get-Command sqlcmd -ErrorAction SilentlyContinue)) {
    throw 'sqlcmd not found. Install SQL Server command line tools and rerun azd provision.'
}

if ($functionPrincipalId) {
    $roleAssignmentsUrl = "https://api.fabric.microsoft.com/v1/workspaces/$workspaceId/roleAssignments"
    $existingRole = az rest `
        --method get `
        --url $roleAssignmentsUrl `
        --resource 'https://api.fabric.microsoft.com' `
        --query "value[?principal.id=='$functionPrincipalId'] | [0].id" `
        --output tsv
    if ($LASTEXITCODE -ne 0) { throw 'Could not read Fabric workspace role assignments.' }

    if (-not $existingRole) {
        Write-Host "Granting Fabric workspace Viewer access to $functionApp ..."
        $body = @{
            principal = @{
                id = $functionPrincipalId
                type = 'ServicePrincipal'
            }
            role = 'Viewer'
        } | ConvertTo-Json -Compress
        az rest `
            --method post `
            --url $roleAssignmentsUrl `
            --resource 'https://api.fabric.microsoft.com' `
            --headers 'Content-Type=application/json' `
            --body $body `
            --output none
        if ($LASTEXITCODE -ne 0) { throw 'Could not grant the Function identity Fabric workspace access.' }
        Start-Sleep -Seconds 10
    }
}

$scripts = @(
    'sql/01_schema.sql',
    'sql/02_staging.sql',
    'sql/03_procedures.sql',
    'sql/04_views.sql',
    'sql/06_security.sql'
)

foreach ($script in $scripts) {
    Write-Host "Applying $script ..."
    # -G with no username uses the current Azure CLI / Entra identity.
    sqlcmd -S $sqlServer -d $sqlDatabase -G -b -i $script
    if ($LASTEXITCODE -ne 0) { throw "Failed applying $script" }
}

# Grant the Function App's managed identity Warehouse permissions.
if ($functionApp) {
    Write-Host "Granting Warehouse access to $functionApp ..."
    $grant = (Get-Content 'sql/05_grants.sql' -Raw).Replace('<FUNCTION_APP_NAME>', $functionApp)
    $tempFile = New-TemporaryFile
    Set-Content -Path $tempFile -Value $grant -Encoding UTF8
    sqlcmd -S $sqlServer -d $sqlDatabase -G -b -i $tempFile
    Remove-Item $tempFile -Force
}

Write-Host ''
Write-Host 'Fabric Warehouse ready. Final manual step — store your GitHub credential:' -ForegroundColor Green
Write-Host "  az keyvault secret set --vault-name $keyVault --name github-credential --value <PAT-or-app-private-key>"
