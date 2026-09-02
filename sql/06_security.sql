/*
    06_security.sql — row-level security scaffolding and personal-data controls.

    This warehouse holds per-developer activity and spend keyed to a GitHub login.

    The RLS policy is CREATED BUT DISABLED so a first deployment works end to end
    without access plumbing. Everything needed to switch it on is already here —
    enabling it is one statement. Decide deliberately who should see individual-level
    rows, then turn it on before sharing reports beyond the project team.

    The erasure and retention procedures below work regardless of the RLS state.

    Run after 01-04. Re-runnable.
*/

IF SCHEMA_ID('rls') IS NULL EXEC('CREATE SCHEMA rls');
GO

-- Members bypass RLS when it is enabled. The Function App identity must be a member,
-- or ingestion cannot read back what it wrote.
IF DATABASE_PRINCIPAL_ID('copilot_metrics_admin') IS NULL
    CREATE ROLE copilot_metrics_admin;
GO

-- Analysts see only the cost centers listed here. Use '*' for enterprise-wide access.
IF OBJECT_ID('dbo.report_access') IS NULL
CREATE TABLE dbo.report_access (
    principal_name SYSNAME       NOT NULL,   -- Entra principal, matches SUSER_SNAME()
    cost_center    VARCHAR(200)  NOT NULL,
    granted_at     DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    granted_by     SYSNAME       NULL,
    CONSTRAINT pk_report_access PRIMARY KEY (principal_name, cost_center)
);
GO

/*
    Filtering dim_user filters every downstream report, because the views all join
    through it. A caller may see a row when they are an admin, when the row is in a
    cost center granted to them, or when the row is their own.
*/
CREATE OR ALTER FUNCTION rls.fn_user_access(@user_login VARCHAR(100), @cost_center VARCHAR(200))
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS access_granted
    WHERE IS_ROLEMEMBER('copilot_metrics_admin') = 1
       OR IS_ROLEMEMBER('db_owner') = 1
       OR @user_login = SUSER_SNAME()
       OR EXISTS (
              SELECT 1
              FROM dbo.report_access ra
              WHERE ra.principal_name = SUSER_SNAME()
                AND (ra.cost_center = @cost_center OR ra.cost_center = '*')
          );
GO

-- Created disabled. Enabling it is a deliberate choice - see the block below.
IF NOT EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'user_access_policy')
    EXEC('
        CREATE SECURITY POLICY rls.user_access_policy
        ADD FILTER PREDICATE rls.fn_user_access(user_login, cost_center) ON dbo.dim_user
        WITH (STATE = OFF);
    ');
GO

/*
    ENABLING ROW-LEVEL SECURITY

    1. Make sure the ingestion identity can still read what it writes:
           ALTER ROLE copilot_metrics_admin ADD MEMBER [<function-app-name>];
       (sql/05_grants.sql already does this.)

    2. Add anyone who needs broad access:
           INSERT INTO dbo.report_access (principal_name, cost_center)
           VALUES ('analyst@contoso.com', 'Platform Engineering');
           -- use '*' for enterprise-wide access

    3. Turn the policy on:
           ALTER SECURITY POLICY rls.user_access_policy WITH (STATE = ON);

    Once enabled, a principal who is not an admin and has no report_access row sees
    zero rows. That is expected, and it is the most common cause of an "empty report"
    after switching it on.

    To turn it back off:
           ALTER SECURITY POLICY rls.user_access_policy WITH (STATE = OFF);

    Check the current state:
           SELECT name, is_enabled FROM sys.security_policies WHERE name = 'user_access_policy';
*/

---------------------------------------------------------------- data subject erasure

/*
    Handles an erasure request. Removes the individual's facts and anonymises the
    dimension row, keeping referential integrity so historical aggregates still sum.
*/
CREATE OR ALTER PROCEDURE dbo.sp_forget_user
    @user_login VARCHAR(100),
    @confirm BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @user_key INT = (SELECT user_key FROM dbo.dim_user WHERE user_login = @user_login);

    IF @user_key IS NULL
    BEGIN
        PRINT 'No such user.';
        RETURN;
    END

    IF @confirm = 0
    BEGIN
        SELECT
            'DRY RUN - pass @confirm = 1 to execute' AS notice,
            (SELECT COUNT(*) FROM dbo.fact_premium_requests WHERE user_key = @user_key) AS billing_rows,
            (SELECT COUNT(*) FROM dbo.fact_user_day WHERE user_key = @user_key) AS activity_rows,
            (SELECT COUNT(*) FROM dbo.fact_user_ide_day WHERE user_key = @user_key) AS ide_rows;
        RETURN;
    END

    BEGIN TRANSACTION;

    DELETE FROM dbo.fact_user_ide_share WHERE user_key = @user_key;
    DELETE FROM dbo.fact_user_ide_day WHERE user_key = @user_key;
    DELETE FROM dbo.fact_user_model_feature_day WHERE user_key = @user_key;
    DELETE FROM dbo.fact_user_team_day WHERE user_key = @user_key;
    DELETE FROM dbo.fact_user_day WHERE user_key = @user_key;
    DELETE FROM dbo.fact_premium_requests WHERE user_key = @user_key;

    UPDATE dbo.dim_user
    SET user_login = CONCAT('erased-', @user_key),
        github_user_id = NULL,
        team_name = NULL,
        cost_center = NULL,
        last_activity_editor = NULL,
        is_active = 0
    WHERE user_key = @user_key;

    COMMIT TRANSACTION;

    PRINT CONCAT('Erased user_key ', @user_key, '. Also delete their raw payloads from the lake.');
END;
GO

---------------------------------------------------------------------- retention

/*
    Personal data should not accumulate indefinitely. Schedule this, or call it
    manually, to drop detail beyond your retention period.

    Billing facts are kept by default because they underpin financial reporting;
    pass @include_billing = 1 if your policy requires their removal too.
*/
CREATE OR ALTER PROCEDURE dbo.sp_purge_personal_data
    @retain_days INT = 730,
    @include_billing BIT = 0,
    @confirm BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @cutoff DATE = DATEADD(DAY, -@retain_days, CAST(SYSUTCDATETIME() AS DATE));
    DECLARE @cutoff_key INT = CONVERT(INT, CONVERT(CHAR(8), @cutoff, 112));

    IF @confirm = 0
    BEGIN
        SELECT
            'DRY RUN - pass @confirm = 1 to execute' AS notice,
            @cutoff AS cutoff_date,
            (SELECT COUNT(*) FROM dbo.fact_user_day WHERE date_key < @cutoff_key) AS activity_rows,
            (SELECT COUNT(*) FROM dbo.fact_user_ide_day WHERE date_key < @cutoff_key) AS ide_rows,
            (SELECT COUNT(*) FROM dbo.fact_premium_requests WHERE date_key < @cutoff_key) AS billing_rows;
        RETURN;
    END

    BEGIN TRANSACTION;

    DELETE FROM dbo.fact_user_ide_share WHERE date_key < @cutoff_key;
    DELETE FROM dbo.fact_user_ide_day WHERE date_key < @cutoff_key;
    DELETE FROM dbo.fact_user_model_feature_day WHERE date_key < @cutoff_key;
    DELETE FROM dbo.fact_user_team_day WHERE date_key < @cutoff_key;
    DELETE FROM dbo.fact_user_day WHERE date_key < @cutoff_key;

    IF @include_billing = 1
        DELETE FROM dbo.fact_premium_requests WHERE date_key < @cutoff_key;

    COMMIT TRANSACTION;

    PRINT CONCAT('Purged personal data older than ', CONVERT(CHAR(10), @cutoff, 23), '.');
END;
GO
