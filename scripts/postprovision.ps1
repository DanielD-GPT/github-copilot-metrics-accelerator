#!/usr/bin/env pwsh
# Applies the SQL objects and reminds the operator to seed the GitHub credential.
# Invoked automatically by `azd up` via the postprovision hook.

$ErrorActionPreference = 'Stop'

$sqlServer   = $env:SQL_SERVER_FQDN
$sqlDatabase = $env:SQL_DATABASE_NAME
$keyVault    = $env:AZURE_KEY_VAULT_NAME
$functionApp = $env:FUNCTION_APP_NAME

if (-not $sqlServer) {
    Write-Warning 'SQL_SERVER_FQDN not set; skipping database setup.'
    exit 0
}

if (-not (Get-Command sqlcmd -ErrorAction SilentlyContinue)) {
    Write-Warning 'sqlcmd not found. Install SQL Server command line tools, then apply sql/*.sql manually.'
    exit 0
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

# Grant the Function App's managed identity access.
if ($functionApp) {
    Write-Host "Granting database access to $functionApp ..."
    $grant = (Get-Content 'sql/05_grants.sql' -Raw).Replace('<FUNCTION_APP_NAME>', $functionApp)
    $tempFile = New-TemporaryFile
    Set-Content -Path $tempFile -Value $grant -Encoding UTF8
    sqlcmd -S $sqlServer -d $sqlDatabase -G -b -i $tempFile
    Remove-Item $tempFile -Force
}

Write-Host ''
Write-Host 'Database ready. Final manual step — store your GitHub credential:' -ForegroundColor Green
Write-Host "  az keyvault secret set --vault-name $keyVault --name github-credential --value <PAT-or-app-private-key>"
