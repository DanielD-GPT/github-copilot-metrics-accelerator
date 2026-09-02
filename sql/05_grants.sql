/*
    05_grants.sql — grant the Function App's managed identity access to the database.

    Run this ONCE, connected as the Entra admin, AFTER `azd up` has created the
    Function App. Replace the placeholder with the Function App name printed by
    `azd env get-values` (FUNCTION_APP_NAME).

    With Entra-only authentication there is no SQL login to fall back on, so
    skipping this step causes the ingestion to fail with "Login failed for user '<token-identified principal>'".
*/

DECLARE @functionAppName SYSNAME = N'<FUNCTION_APP_NAME>';
DECLARE @sql NVARCHAR(MAX);

IF @functionAppName = N'<FUNCTION_APP_NAME>'
BEGIN
    RAISERROR('Replace <FUNCTION_APP_NAME> with the actual Function App name before running.', 16, 1);
    RETURN;
END

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @functionAppName)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@functionAppName) + N' FROM EXTERNAL PROVIDER;';
    EXEC sp_executesql @sql;
END

-- Least privilege: read/write data and execute the load procedures. No DDL.
-- copilot_metrics_admin exempts the ingestion identity from row-level security,
-- which it needs in order to read back the rows it writes.
SET @sql = N'
    ALTER ROLE db_datareader ADD MEMBER ' + QUOTENAME(@functionAppName) + N';
    ALTER ROLE db_datawriter ADD MEMBER ' + QUOTENAME(@functionAppName) + N';
    GRANT EXECUTE ON SCHEMA::dbo TO ' + QUOTENAME(@functionAppName) + N';';
EXEC sp_executesql @sql;

IF DATABASE_PRINCIPAL_ID('copilot_metrics_admin') IS NOT NULL
BEGIN
    SET @sql = N'ALTER ROLE copilot_metrics_admin ADD MEMBER ' + QUOTENAME(@functionAppName) + N';';
    EXEC sp_executesql @sql;
END

PRINT 'Granted database access to ' + @functionAppName;
GO
