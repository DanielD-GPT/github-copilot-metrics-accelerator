/*
    03_procedures.sql — idempotent MERGE loads.

    sp_load_all is the single entry point called by the Function App.
    Re-running it for an overlapping window is safe and expected: GitHub
    posts billing corrections for several days after the fact.
*/

------------------------------------------------------------------- dimensions

CREATE OR ALTER PROCEDURE dbo.sp_merge_dimensions
AS
BEGIN
    SET NOCOUNT ON;

    -- orgs
    INSERT INTO dbo.dim_org (org_login)
    SELECT DISTINCT s.org_login
    FROM (
        SELECT org_login FROM stg.user_day
        UNION SELECT org_login FROM stg.user_ide
        UNION SELECT org_login FROM stg.user_model_feature
        UNION SELECT org_login FROM stg.user_teams
        UNION SELECT org_login FROM stg.premium_requests
        UNION SELECT org_login FROM stg.seats
    ) s
    WHERE NULLIF(s.org_login, '') IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dbo.dim_org d WHERE d.org_login = s.org_login);

    -- editors, keyed on the raw `ide` value with a friendly family label
    INSERT INTO dbo.dim_editor (editor_name, editor_family)
    SELECT DISTINCT e.ide, MAX(e.ide_family)
    FROM stg.user_ide e
    WHERE NULLIF(e.ide, '') IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dbo.dim_editor d WHERE d.editor_name = e.ide)
    GROUP BY e.ide;

    -- features, grouped for reporting
    INSERT INTO dbo.dim_feature (feature, feature_group)
    SELECT DISTINCT
        f.feature,
        CASE
            WHEN f.feature = 'code_completion' THEN 'Completions'
            WHEN f.feature = 'agent_edit'      THEN 'Agent'
            WHEN f.feature = 'copilot_cli'     THEN 'CLI'
            WHEN f.feature = 'copilot_app'     THEN 'App'
            WHEN f.feature LIKE 'chat%'        THEN 'Chat'
            ELSE 'Other'
        END
    FROM stg.user_model_feature f
    WHERE NULLIF(f.feature, '') IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dbo.dim_feature d WHERE d.feature = f.feature);

    -- models seen in either activity or billing
    INSERT INTO dbo.dim_model (model_name, is_custom_model)
    SELECT s.model_name, 0
    FROM (
        SELECT model_name FROM stg.user_model_feature
        UNION SELECT model_name FROM stg.premium_requests
    ) s
    WHERE NULLIF(s.model_name, '') IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dbo.dim_model d WHERE d.model_name = s.model_name);

    -- products / SKUs
    INSERT INTO dbo.dim_product (product, sku, unit_type)
    SELECT DISTINCT p.product, p.sku, MAX(p.unit_type)
    FROM stg.premium_requests p
    WHERE NULLIF(p.product, '') IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM dbo.dim_product d
          WHERE d.product = p.product AND d.sku = p.sku
      )
    GROUP BY p.product, p.sku;

    -- users: seats carry team and activity metadata; metrics and billing add anyone missing
    MERGE dbo.dim_user AS tgt
    USING (
        SELECT
            user_login,
            MAX(user_id)              AS user_id,
            MAX(team_name)            AS team_name,
            MAX(plan_type)            AS plan_type,
            MAX(created_at)           AS created_at,
            MAX(last_activity_at)     AS last_activity_at,
            MAX(last_activity_editor) AS last_activity_editor
        FROM stg.seats
        WHERE NULLIF(user_login, '') IS NOT NULL
        GROUP BY user_login
    ) AS src
    ON tgt.user_login = src.user_login
    WHEN MATCHED THEN UPDATE SET
        tgt.github_user_id       = COALESCE(src.user_id, tgt.github_user_id),
        tgt.team_name            = COALESCE(src.team_name, tgt.team_name),
        tgt.plan_type            = COALESCE(src.plan_type, tgt.plan_type),
        tgt.seat_created_at      = COALESCE(src.created_at, tgt.seat_created_at),
        tgt.last_activity_at     = COALESCE(src.last_activity_at, tgt.last_activity_at),
        tgt.last_activity_editor = COALESCE(src.last_activity_editor, tgt.last_activity_editor),
        tgt.is_active            = 1
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (user_login, github_user_id, team_name, plan_type,
                seat_created_at, last_activity_at, last_activity_editor, is_active)
        VALUES (src.user_login, src.user_id, src.team_name, src.plan_type,
                src.created_at, src.last_activity_at, src.last_activity_editor, 1);

    INSERT INTO dbo.dim_user (user_login, github_user_id, is_active)
    SELECT s.user_login, MAX(s.user_id), 1
    FROM (
        SELECT user_login, user_id FROM stg.user_day
        UNION ALL SELECT user_login, NULL FROM stg.premium_requests
        UNION ALL SELECT user_login, user_id FROM stg.user_teams
    ) s
    WHERE NULLIF(s.user_login, '') IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dbo.dim_user d WHERE d.user_login = s.user_login)
    GROUP BY s.user_login;

    -- teams
    INSERT INTO dbo.dim_team (team_id, team_slug, org_key)
    SELECT DISTINCT t.team_id, MAX(t.team_slug), o.org_key
    FROM stg.user_teams t
    JOIN dbo.dim_org o ON o.org_login = t.org_login
    WHERE t.team_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM dbo.dim_team d
          WHERE d.team_id = t.team_id AND d.org_key = o.org_key
      )
    GROUP BY t.team_id, o.org_key;
END;
GO

----------------------------------------------------------------- fact: dollars

CREATE OR ALTER PROCEDURE dbo.sp_merge_premium_requests
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dbo.fact_premium_requests AS tgt
    USING (
        SELECT
            dd.date_key,
            du.user_key,
            dorg.org_key,
            dm.model_key,
            dp.product_key,
            SUM(COALESCE(p.net_quantity, 0))    AS net_quantity,
            SUM(COALESCE(p.gross_quantity, 0))  AS gross_quantity,
            MAX(COALESCE(p.price_per_unit, 0))  AS price_per_unit,
            SUM(COALESCE(p.gross_amount, 0))    AS gross_amount,
            SUM(COALESCE(p.discount_amount, 0)) AS discount_amount,
            SUM(COALESCE(p.net_amount, 0))      AS net_amount
        FROM stg.premium_requests p
        JOIN dbo.dim_date    dd   ON dd.[date] = p.usage_date
        JOIN dbo.dim_user    du   ON du.user_login = p.user_login
        JOIN dbo.dim_org     dorg ON dorg.org_login = p.org_login
        JOIN dbo.dim_model   dm   ON dm.model_name = p.model_name
        JOIN dbo.dim_product dp   ON dp.product = p.product AND dp.sku = p.sku
        GROUP BY dd.date_key, du.user_key, dorg.org_key, dm.model_key, dp.product_key
    ) AS src
    ON  tgt.date_key    = src.date_key
    AND tgt.user_key    = src.user_key
    AND tgt.org_key     = src.org_key
    AND tgt.model_key   = src.model_key
    AND tgt.product_key = src.product_key
    WHEN MATCHED THEN UPDATE SET
        tgt.net_quantity    = src.net_quantity,
        tgt.gross_quantity  = src.gross_quantity,
        tgt.price_per_unit  = src.price_per_unit,
        tgt.gross_amount    = src.gross_amount,
        tgt.discount_amount = src.discount_amount,
        tgt.net_amount      = src.net_amount,
        tgt.loaded_at       = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (date_key, user_key, org_key, model_key, product_key,
                net_quantity, gross_quantity, price_per_unit,
                gross_amount, discount_amount, net_amount, loaded_at)
        VALUES (src.date_key, src.user_key, src.org_key, src.model_key, src.product_key,
                src.net_quantity, src.gross_quantity, src.price_per_unit,
                src.gross_amount, src.discount_amount, src.net_amount, SYSUTCDATETIME());
END;
GO

-------------------------------------------------------------- fact: engagement

CREATE OR ALTER PROCEDURE dbo.sp_merge_user_activity
AS
BEGIN
    SET NOCOUNT ON;

    -- user x day
    MERGE dbo.fact_user_day AS tgt
    USING (
        SELECT
            dd.date_key, du.user_key, dorg.org_key,
            SUM(COALESCE(s.ai_credits_used, 0))       AS ai_credits_used,
            MAX(s.adoption_phase)                     AS adoption_phase,
            MAX(s.adoption_phase_number)              AS adoption_phase_number,
            MAX(CONVERT(TINYINT, s.used_agent))               AS used_agent,
            MAX(CONVERT(TINYINT, s.used_chat))                AS used_chat,
            MAX(CONVERT(TINYINT, s.used_cli))                 AS used_cli,
            MAX(CONVERT(TINYINT, s.used_copilot_app))         AS used_copilot_app,
            MAX(CONVERT(TINYINT, s.used_copilot_cloud_agent)) AS used_copilot_cloud_agent,
            MAX(CONVERT(TINYINT, s.used_code_review_active))  AS used_code_review_active,
            MAX(CONVERT(TINYINT, s.used_code_review_passive)) AS used_code_review_passive,
            SUM(s.user_initiated_interaction_count)   AS interactions,
            SUM(s.code_generation_activity_count)     AS generations,
            SUM(s.code_acceptance_activity_count)     AS acceptances,
            SUM(s.loc_suggested_to_add_sum)           AS loc_suggested,
            SUM(s.loc_added_sum)                      AS loc_added,
            SUM(s.loc_deleted_sum)                    AS loc_deleted
        FROM stg.user_day s
        JOIN dbo.dim_date dd   ON dd.[date] = s.activity_date
        JOIN dbo.dim_user du   ON du.user_login = s.user_login
        JOIN dbo.dim_org  dorg ON dorg.org_login = s.org_login
        GROUP BY dd.date_key, du.user_key, dorg.org_key
    ) AS src
    ON tgt.date_key = src.date_key AND tgt.user_key = src.user_key AND tgt.org_key = src.org_key
    WHEN MATCHED THEN UPDATE SET
        tgt.ai_credits_used = src.ai_credits_used,
        tgt.adoption_phase = src.adoption_phase,
        tgt.adoption_phase_number = src.adoption_phase_number,
        tgt.used_agent = src.used_agent,
        tgt.used_chat = src.used_chat,
        tgt.used_cli = src.used_cli,
        tgt.used_copilot_app = src.used_copilot_app,
        tgt.used_copilot_cloud_agent = src.used_copilot_cloud_agent,
        tgt.used_code_review_active = src.used_code_review_active,
        tgt.used_code_review_passive = src.used_code_review_passive,
        tgt.user_initiated_interaction_count = src.interactions,
        tgt.code_generation_activity_count = src.generations,
        tgt.code_acceptance_activity_count = src.acceptances,
        tgt.loc_suggested_to_add_sum = src.loc_suggested,
        tgt.loc_added_sum = src.loc_added,
        tgt.loc_deleted_sum = src.loc_deleted,
        tgt.loaded_at = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (date_key, user_key, org_key, ai_credits_used, adoption_phase, adoption_phase_number,
                used_agent, used_chat, used_cli, used_copilot_app, used_copilot_cloud_agent,
                used_code_review_active, used_code_review_passive,
                user_initiated_interaction_count, code_generation_activity_count,
                code_acceptance_activity_count, loc_suggested_to_add_sum, loc_added_sum,
                loc_deleted_sum, loaded_at)
        VALUES (src.date_key, src.user_key, src.org_key, src.ai_credits_used, src.adoption_phase,
                src.adoption_phase_number, src.used_agent, src.used_chat, src.used_cli,
                src.used_copilot_app, src.used_copilot_cloud_agent, src.used_code_review_active,
                src.used_code_review_passive, src.interactions, src.generations, src.acceptances,
                src.loc_suggested, src.loc_added, src.loc_deleted, SYSUTCDATETIME());

    -- user x day x ide
    MERGE dbo.fact_user_ide_day AS tgt
    USING (
        SELECT
            dd.date_key, du.user_key, dorg.org_key, de.editor_key,
            MAX(s.last_known_ide_version)           AS ide_version,
            MAX(s.last_known_plugin_version)        AS plugin_version,
            SUM(s.user_initiated_interaction_count) AS interactions,
            SUM(s.code_generation_activity_count)   AS generations,
            SUM(s.code_acceptance_activity_count)   AS acceptances,
            SUM(s.loc_added_sum)                    AS loc_added,
            SUM(s.loc_deleted_sum)                  AS loc_deleted
        FROM stg.user_ide s
        JOIN dbo.dim_date   dd   ON dd.[date] = s.activity_date
        JOIN dbo.dim_user   du   ON du.user_login = s.user_login
        JOIN dbo.dim_org    dorg ON dorg.org_login = s.org_login
        JOIN dbo.dim_editor de   ON de.editor_name = s.ide
        GROUP BY dd.date_key, du.user_key, dorg.org_key, de.editor_key
    ) AS src
    ON  tgt.date_key = src.date_key AND tgt.user_key = src.user_key
    AND tgt.org_key = src.org_key AND tgt.editor_key = src.editor_key
    WHEN MATCHED THEN UPDATE SET
        tgt.last_known_ide_version = src.ide_version,
        tgt.last_known_plugin_version = src.plugin_version,
        tgt.user_initiated_interaction_count = src.interactions,
        tgt.code_generation_activity_count = src.generations,
        tgt.code_acceptance_activity_count = src.acceptances,
        tgt.loc_added_sum = src.loc_added,
        tgt.loc_deleted_sum = src.loc_deleted,
        tgt.loaded_at = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (date_key, user_key, org_key, editor_key, last_known_ide_version,
                last_known_plugin_version, user_initiated_interaction_count,
                code_generation_activity_count, code_acceptance_activity_count,
                loc_added_sum, loc_deleted_sum, loaded_at)
        VALUES (src.date_key, src.user_key, src.org_key, src.editor_key, src.ide_version,
                src.plugin_version, src.interactions, src.generations, src.acceptances,
                src.loc_added, src.loc_deleted, SYSUTCDATETIME());

    -- user x day x model x feature
    MERGE dbo.fact_user_model_feature_day AS tgt
    USING (
        SELECT
            dd.date_key, du.user_key, dorg.org_key, dm.model_key, df.feature_key,
            SUM(s.user_initiated_interaction_count) AS interactions,
            SUM(s.code_generation_activity_count)   AS generations,
            SUM(s.code_acceptance_activity_count)   AS acceptances
        FROM stg.user_model_feature s
        JOIN dbo.dim_date    dd   ON dd.[date] = s.activity_date
        JOIN dbo.dim_user    du   ON du.user_login = s.user_login
        JOIN dbo.dim_org     dorg ON dorg.org_login = s.org_login
        JOIN dbo.dim_model   dm   ON dm.model_name = s.model_name
        JOIN dbo.dim_feature df   ON df.feature = s.feature
        GROUP BY dd.date_key, du.user_key, dorg.org_key, dm.model_key, df.feature_key
    ) AS src
    ON  tgt.date_key = src.date_key AND tgt.user_key = src.user_key AND tgt.org_key = src.org_key
    AND tgt.model_key = src.model_key AND tgt.feature_key = src.feature_key
    WHEN MATCHED THEN UPDATE SET
        tgt.user_initiated_interaction_count = src.interactions,
        tgt.code_generation_activity_count = src.generations,
        tgt.code_acceptance_activity_count = src.acceptances,
        tgt.loaded_at = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (date_key, user_key, org_key, model_key, feature_key,
                user_initiated_interaction_count, code_generation_activity_count,
                code_acceptance_activity_count, loaded_at)
        VALUES (src.date_key, src.user_key, src.org_key, src.model_key, src.feature_key,
                src.interactions, src.generations, src.acceptances, SYSUTCDATETIME());

    -- user x day x team
    MERGE dbo.fact_user_team_day AS tgt
    USING (
        SELECT DISTINCT dd.date_key, du.user_key, dt.team_key
        FROM stg.user_teams s
        JOIN dbo.dim_date dd   ON dd.[date] = s.activity_date
        JOIN dbo.dim_user du   ON du.user_login = s.user_login
        JOIN dbo.dim_org  dorg ON dorg.org_login = s.org_login
        JOIN dbo.dim_team dt   ON dt.team_id = s.team_id AND dt.org_key = dorg.org_key
    ) AS src
    ON tgt.date_key = src.date_key AND tgt.user_key = src.user_key AND tgt.team_key = src.team_key
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (date_key, user_key, team_key, loaded_at)
        VALUES (src.date_key, src.user_key, src.team_key, SYSUTCDATETIME());
END;
GO

------------------------------------------------------- allocation bridge build

/*
    Build the per-user IDE share.

    Weight each IDE by the individual's own activity that day, then normalise within
    (date, user, org). Preference order for the weight signal:
      1. user_initiated_interaction_count - explicit prompts, closest proxy for a premium request
      2. code_generation_activity_count   - fallback when interactions are all zero
      3. equal split                      - last resort, so spend is never silently dropped
*/
CREATE OR ALTER PROCEDURE dbo.sp_build_user_ide_share
    @since DATE = NULL,
    @until DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF @since IS NULL SELECT @since = MIN(activity_date) FROM stg.user_ide;
    IF @until IS NULL SELECT @until = MAX(activity_date) FROM stg.user_ide;
    IF @since IS NULL RETURN;

    DECLARE @since_key INT = CONVERT(INT, CONVERT(CHAR(8), @since, 112));
    DECLARE @until_key INT = CONVERT(INT, CONVERT(CHAR(8), @until, 112));

    DELETE FROM dbo.fact_user_ide_share
    WHERE date_key BETWEEN @since_key AND @until_key;

    ;WITH scoped AS (
        SELECT date_key, user_key, org_key, editor_key,
               user_initiated_interaction_count AS interactions,
               code_generation_activity_count   AS generations
        FROM dbo.fact_user_ide_day
        WHERE date_key BETWEEN @since_key AND @until_key
    ),
    basis AS (
        SELECT date_key, user_key, org_key,
               SUM(interactions) AS total_interactions,
               SUM(generations)  AS total_generations,
               COUNT(*)          AS ide_count
        FROM scoped
        GROUP BY date_key, user_key, org_key
    ),
    weighted AS (
        SELECT
            s.date_key, s.user_key, s.org_key, s.editor_key,
            CASE
                WHEN b.total_interactions > 0 THEN CAST(s.interactions AS DECIMAL(18,6)) / b.total_interactions
                WHEN b.total_generations  > 0 THEN CAST(s.generations  AS DECIMAL(18,6)) / b.total_generations
                ELSE 1.0 / NULLIF(b.ide_count, 0)
            END AS share,
            CASE
                WHEN b.total_interactions > 0 THEN 'user_interactions'
                WHEN b.total_generations  > 0 THEN 'code_generations'
                ELSE 'equal_split'
            END AS weight_basis
        FROM scoped s
        JOIN basis b
          ON b.date_key = s.date_key AND b.user_key = s.user_key AND b.org_key = s.org_key
    )
    INSERT INTO dbo.fact_user_ide_share (
        date_key, user_key, org_key, editor_key, share, weight_basis, loaded_at
    )
    SELECT date_key, user_key, org_key, editor_key,
           CAST(share AS DECIMAL(9,8)), weight_basis, SYSUTCDATETIME()
    FROM weighted
    WHERE share IS NOT NULL AND share > 0;
END;
GO

------------------------------------------------------------- reconciliation

/*
    Fail loudly rather than reporting success on an empty or broken load.
    Raises when staging holds rows that never reached a fact table, which is the
    signature of a dimension join dropping data.
*/
CREATE OR ALTER PROCEDURE dbo.sp_reconcile_load
    @strict BIT = 1
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @stg_billing INT = (SELECT COUNT(*) FROM stg.premium_requests);
    DECLARE @stg_user_day INT = (SELECT COUNT(*) FROM stg.user_day);
    DECLARE @stg_ide INT = (SELECT COUNT(*) FROM stg.user_ide);
    DECLARE @stg_seats INT = (SELECT COUNT(*) FROM stg.seats);

    DECLARE @orphan_billing INT = (
        SELECT COUNT(*)
        FROM stg.premium_requests p
        WHERE NOT EXISTS (
            SELECT 1
            FROM dbo.dim_user du
            JOIN dbo.dim_org dorg ON dorg.org_login = p.org_login
            JOIN dbo.dim_model dm ON dm.model_name = p.model_name
            JOIN dbo.dim_product dp ON dp.product = p.product AND dp.sku = p.sku
            WHERE du.user_login = p.user_login
        )
    );

    DECLARE @message NVARCHAR(2000) = CONCAT(
        'staging rows -> user_day:', @stg_user_day,
        ' user_ide:', @stg_ide,
        ' seats:', @stg_seats,
        ' billing:', @stg_billing,
        ' orphaned_billing:', @orphan_billing);

    PRINT @message;

    IF @strict = 1 AND @orphan_billing > 0
        THROW 51001, 'Billing rows could not be mapped to dimensions; load is incomplete.', 1;

    IF @strict = 1 AND @stg_user_day = 0 AND @stg_billing = 0
        THROW 51002, 'No metrics and no billing rows were staged; the extract returned nothing.', 1;

    -- Seats drive the billing fan-out. Activity without seats means the seats call
    -- failed, and every dollar would silently land as zero.
    IF @strict = 1 AND @stg_user_day > 0 AND @stg_seats = 0
        THROW 51005, 'Metrics staged but no Copilot seats; spend cannot be attributed. Check billing permissions.', 1;

    -- Zero spend can be legitimate, so warn rather than fail.
    IF @stg_seats > 0 AND @stg_billing = 0
        PRINT 'WARNING: seats present but no billing rows staged. Verify premium request permissions.';

    SELECT @stg_user_day AS rows_user_day, @stg_ide AS rows_user_ide,
           @stg_seats AS rows_seats, @stg_billing AS rows_premium_requests,
           @orphan_billing AS orphaned_billing;
END;
GO

------------------------------------------------------------- run concurrency

/*
    Ingestion runs share staging tables, so the Function acquires a renewable Azure
    Blob lease before calling this procedure. Fabric Warehouse doesn't support
    sp_getapplock; this procedure records status only.
*/
CREATE OR ALTER PROCEDURE dbo.sp_begin_run
    @run_id VARCHAR(36),
    @trigger_source VARCHAR(30),
    @since DATE,
    @until DATE,
    @stale_after_minutes INT = 120
AS
BEGIN
    SET NOCOUNT ON;

    -- A crashed run leaves its row 'running' forever; retire it after the threshold.
    UPDATE dbo.ingestion_run
    SET status = 'abandoned',
        completed_at = SYSUTCDATETIME(),
        message = 'Abandoned: exceeded stale threshold without completing.'
    WHERE status = 'running'
      AND started_at < DATEADD(MINUTE, -@stale_after_minutes, SYSUTCDATETIME());

    INSERT INTO dbo.ingestion_run (
        run_id, started_at, since_date, until_date, trigger_source, status
    )
    VALUES (@run_id, SYSUTCDATETIME(), @since, @until, @trigger_source, 'running');
END;
GO

CREATE OR ALTER PROCEDURE dbo.sp_complete_run
    @run_id VARCHAR(36),
    @status VARCHAR(20),
    @rows_user_day INT = NULL,
    @rows_user_ide INT = NULL,
    @rows_model_feature INT = NULL,
    @rows_premium_requests INT = NULL,
    @rows_seats INT = NULL,
    @message NVARCHAR(2000) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.ingestion_run
    SET completed_at = SYSUTCDATETIME(),
        status = @status,
        rows_user_day = @rows_user_day,
        rows_user_ide = @rows_user_ide,
        rows_model_feature = @rows_model_feature,
        rows_premium_requests = @rows_premium_requests,
        rows_seats = @rows_seats,
        message = @message
    WHERE run_id = @run_id;
END;
GO

------------------------------------------------------------------ orchestrator

CREATE OR ALTER PROCEDURE dbo.sp_load_all
    @strict BIT = 1
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    EXEC dbo.sp_merge_dimensions;
    EXEC dbo.sp_merge_user_activity;
    EXEC dbo.sp_merge_premium_requests;
    EXEC dbo.sp_build_user_ide_share;
    EXEC dbo.sp_reconcile_load @strict = @strict;
END;
GO
