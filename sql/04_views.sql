/*
    04_views.sql — reporting layer.

    ACCURACY NOTICE
    ---------------
    user x model x $        -> EXACT. From the per-user premium request billing report.
    ... x feature           -> MODELLED, from the user's own model x feature activity.
    ... x editor (ide)      -> MODELLED, from the user's own per-IDE activity.

    GitHub publishes totals_by_ide and totals_by_model_feature as sibling arrays, not
    as an ide x model cross-product. The editor split therefore assumes
    P(ide | user, day, model) is approximated by P(ide | user, day).

    This is materially better than an org-wide average because every weight comes from
    the individual's own activity — but it is still an estimate, not an audit record.
*/

------------------------------------------- headline: spend by user, model, editor

CREATE OR ALTER VIEW dbo.vw_spend_by_user_model_editor
AS
WITH feature_share AS (
    -- Split each user's per-model spend across the features they used that model for.
    SELECT
        mf.date_key, mf.user_key, mf.org_key, mf.model_key, mf.feature_key,
        CAST(
            CASE
                WHEN SUM(SUM(mf.user_initiated_interaction_count))
                       OVER (PARTITION BY mf.date_key, mf.user_key, mf.org_key, mf.model_key) > 0
                THEN SUM(mf.user_initiated_interaction_count) * 1.0
                     / SUM(SUM(mf.user_initiated_interaction_count))
                         OVER (PARTITION BY mf.date_key, mf.user_key, mf.org_key, mf.model_key)
                ELSE 1.0 / COUNT(*) OVER (PARTITION BY mf.date_key, mf.user_key, mf.org_key, mf.model_key)
            END AS DECIMAL(18,8)) AS share
    FROM dbo.fact_user_model_feature_day mf
    GROUP BY mf.date_key, mf.user_key, mf.org_key, mf.model_key, mf.feature_key
)
SELECT
    d.[date]      AS usage_date,
    d.year_month,
    u.user_login,
    u.team_name,
    u.cost_center,
    o.org_login,
    m.model_name,
    p.product,
    p.sku,
    COALESCE(de.editor_name, 'unattributed')   AS editor_name,
    COALESCE(de.editor_family, 'Unattributed') AS editor_family,
    COALESCE(df.feature, 'unattributed')       AS feature,
    COALESCE(df.feature_group, 'Unattributed') AS feature_group,
    COALESCE(ish.share, 1.0)                   AS ide_share,
    COALESCE(fs.share, 1.0)                    AS feature_share,
    ish.weight_basis,
    -- Only allocated measures are exposed here. The unallocated total fans out across
    -- editor and feature rows, so summing it would overstate spend.
    CAST(f.net_amount   * COALESCE(ish.share, 1.0) * COALESCE(fs.share, 1.0) AS DECIMAL(18,6)) AS allocated_net_amount,
    CAST(f.gross_amount * COALESCE(ish.share, 1.0) * COALESCE(fs.share, 1.0) AS DECIMAL(18,6)) AS allocated_gross_amount,
    CAST(f.net_quantity * COALESCE(ish.share, 1.0) * COALESCE(fs.share, 1.0) AS DECIMAL(18,6)) AS allocated_quantity,
    CASE
        WHEN ish.share IS NULL AND fs.share IS NULL THEN 'exact-unallocated'
        WHEN ish.share IS NULL THEN 'feature-only'
        ELSE 'modelled'
    END AS attribution_quality
FROM dbo.fact_premium_requests f
JOIN dbo.dim_date    d ON d.date_key    = f.date_key
JOIN dbo.dim_user    u ON u.user_key    = f.user_key
JOIN dbo.dim_org     o ON o.org_key     = f.org_key
JOIN dbo.dim_model   m ON m.model_key   = f.model_key
JOIN dbo.dim_product p ON p.product_key = f.product_key
LEFT JOIN dbo.fact_user_ide_share ish
       ON  ish.date_key = f.date_key
       AND ish.user_key = f.user_key
       AND ish.org_key  = f.org_key
LEFT JOIN dbo.dim_editor de ON de.editor_key = ish.editor_key
LEFT JOIN feature_share fs
       ON  fs.date_key  = f.date_key
       AND fs.user_key  = f.user_key
       AND fs.org_key   = f.org_key
       AND fs.model_key = f.model_key
LEFT JOIN dbo.dim_feature df ON df.feature_key = fs.feature_key;
GO

------------------------------------------------- exact spend, no modelling at all

CREATE OR ALTER VIEW dbo.vw_spend_by_user_model
AS
SELECT
    d.[date]      AS usage_date,
    d.year_month,
    u.user_login,
    u.team_name,
    u.cost_center,
    o.org_login,
    m.model_name,
    p.sku,
    SUM(f.net_quantity)    AS quantity,
    SUM(f.gross_amount)    AS gross_amount,
    SUM(f.discount_amount) AS discount_amount,
    SUM(f.net_amount)      AS net_amount
FROM dbo.fact_premium_requests f
JOIN dbo.dim_date    d ON d.date_key    = f.date_key
JOIN dbo.dim_user    u ON u.user_key    = f.user_key
JOIN dbo.dim_org     o ON o.org_key     = f.org_key
JOIN dbo.dim_model   m ON m.model_key   = f.model_key
JOIN dbo.dim_product p ON p.product_key = f.product_key
GROUP BY d.[date], d.year_month, u.user_login, u.team_name, u.cost_center,
         o.org_login, m.model_name, p.sku;
GO

------------------------------------------------------------ monthly rollups

CREATE OR ALTER VIEW dbo.vw_monthly_spend_by_team
AS
SELECT
    d.year_month,
    COALESCE(u.cost_center, u.team_name, 'unassigned') AS cost_center,
    o.org_login,
    m.model_name,
    COUNT(DISTINCT u.user_key) AS active_users,
    SUM(f.net_amount)          AS net_amount
FROM dbo.fact_premium_requests f
JOIN dbo.dim_date  d ON d.date_key  = f.date_key
JOIN dbo.dim_user  u ON u.user_key  = f.user_key
JOIN dbo.dim_org   o ON o.org_key   = f.org_key
JOIN dbo.dim_model m ON m.model_key = f.model_key
GROUP BY d.year_month, COALESCE(u.cost_center, u.team_name, 'unassigned'),
         o.org_login, m.model_name;
GO

CREATE OR ALTER VIEW dbo.vw_editor_mix
AS
SELECT
    d.[date] AS activity_date,
    d.year_month,
    o.org_login,
    de.editor_name,
    de.editor_family,
    COUNT(DISTINCT e.user_key)                  AS active_users,
    SUM(e.user_initiated_interaction_count)     AS interactions,
    SUM(e.code_generation_activity_count)       AS generations,
    SUM(e.code_acceptance_activity_count)       AS acceptances,
    SUM(e.loc_added_sum)                        AS loc_added,
    SUM(e.loc_deleted_sum)                      AS loc_deleted,
    CASE WHEN SUM(e.code_generation_activity_count) > 0
         THEN CAST(SUM(e.code_acceptance_activity_count) * 1.0
                   / SUM(e.code_generation_activity_count) AS DECIMAL(9,4))
         ELSE NULL END AS acceptance_rate
FROM dbo.fact_user_ide_day e
JOIN dbo.dim_date   d  ON d.date_key    = e.date_key
JOIN dbo.dim_org    o  ON o.org_key     = e.org_key
JOIN dbo.dim_editor de ON de.editor_key = e.editor_key
GROUP BY d.[date], d.year_month, o.org_login, de.editor_name, de.editor_family;
GO

-- Model and feature mix per user-day, straight from GitHub's own breakdown.
CREATE OR ALTER VIEW dbo.vw_model_feature_mix
AS
SELECT
    d.[date] AS activity_date,
    d.year_month,
    o.org_login,
    u.user_login,
    m.model_name,
    df.feature,
    df.feature_group,
    SUM(mf.user_initiated_interaction_count) AS interactions,
    SUM(mf.code_generation_activity_count)   AS generations,
    SUM(mf.code_acceptance_activity_count)   AS acceptances
FROM dbo.fact_user_model_feature_day mf
JOIN dbo.dim_date    d  ON d.date_key    = mf.date_key
JOIN dbo.dim_org     o  ON o.org_key     = mf.org_key
JOIN dbo.dim_user    u  ON u.user_key    = mf.user_key
JOIN dbo.dim_model   m  ON m.model_key   = mf.model_key
JOIN dbo.dim_feature df ON df.feature_key = mf.feature_key
GROUP BY d.[date], d.year_month, o.org_login, u.user_login,
         m.model_name, df.feature, df.feature_group;
GO

-- Team rollup via GitHub's user-teams report.
-- Teams with fewer than 5 seated users are omitted by GitHub, so totals here will
-- not reconcile to enterprise spend. Use vw_spend_by_user_model for a complete total.
CREATE OR ALTER VIEW dbo.vw_spend_by_team
AS
SELECT
    d.year_month,
    o.org_login,
    t.team_slug,
    m.model_name,
    COUNT(DISTINCT f.user_key) AS active_users,
    SUM(f.net_amount)          AS net_amount
FROM dbo.fact_premium_requests f
JOIN dbo.dim_date d ON d.date_key = f.date_key
JOIN dbo.dim_org  o ON o.org_key  = f.org_key
JOIN dbo.dim_model m ON m.model_key = f.model_key
JOIN dbo.fact_user_team_day ut
  ON ut.date_key = f.date_key AND ut.user_key = f.user_key
JOIN dbo.dim_team t ON t.team_key = ut.team_key
GROUP BY d.year_month, o.org_login, t.team_slug, m.model_name;
GO

------------------------------------------------------- seats with no consumption

CREATE OR ALTER VIEW dbo.vw_inactive_seats
AS
SELECT
    u.user_login,
    u.team_name,
    u.plan_type,
    u.last_activity_at,
    DATEDIFF(DAY, u.last_activity_at, SYSUTCDATETIME()) AS days_since_activity,
    COALESCE(spend.net_amount_30d, 0) AS net_amount_30d
FROM dbo.dim_user u
OUTER APPLY (
    SELECT SUM(f.net_amount) AS net_amount_30d
    FROM dbo.fact_premium_requests f
    JOIN dbo.dim_date d ON d.date_key = f.date_key
    WHERE f.user_key = u.user_key
      AND d.[date] >= DATEADD(DAY, -30, CAST(SYSUTCDATETIME() AS DATE))
) spend
WHERE u.is_active = 1
  AND (u.last_activity_at IS NULL OR u.last_activity_at < DATEADD(DAY, -30, SYSUTCDATETIME()));
GO
