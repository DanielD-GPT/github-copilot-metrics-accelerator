/*
    01_schema.sql — dimensional model
    Run order: 01_schema -> 02_staging -> 03_procedures -> 04_views
*/

IF SCHEMA_ID('stg') IS NULL EXEC('CREATE SCHEMA stg');
GO

-------------------------------------------------------------------- dimensions

IF OBJECT_ID('dbo.dim_date') IS NULL
CREATE TABLE dbo.dim_date (
    date_key        INT          NOT NULL,   -- yyyymmdd
    [date]          DATE         NOT NULL,
    [year]          SMALLINT     NOT NULL,
    [month]         SMALLINT     NOT NULL,
    [day]           SMALLINT     NOT NULL,
    month_name      VARCHAR(20)  NOT NULL,
    year_month      CHAR(7)      NOT NULL,
    day_of_week     SMALLINT     NOT NULL,
    is_weekend      BIT          NOT NULL
);
GO

IF OBJECT_ID('dbo.dim_org') IS NULL
CREATE TABLE dbo.dim_org (
    org_key         BIGINT IDENTITY NOT NULL,
    org_login       VARCHAR(100) NOT NULL
);
GO

IF OBJECT_ID('dbo.dim_user') IS NULL
CREATE TABLE dbo.dim_user (
    user_key        BIGINT IDENTITY NOT NULL,
    user_login      VARCHAR(100) NOT NULL,
    github_user_id  BIGINT       NULL,
    team_name       VARCHAR(200) NULL,
    cost_center     VARCHAR(200) NULL,   -- populated by the customer
    plan_type       VARCHAR(50)  NULL,
    seat_created_at DATETIME2(0) NULL,
    last_activity_at DATETIME2(0) NULL,
    last_activity_editor VARCHAR(100) NULL,
    is_active       BIT NOT NULL
);
GO

IF OBJECT_ID('dbo.dim_model') IS NULL
CREATE TABLE dbo.dim_model (
    model_key       BIGINT IDENTITY NOT NULL,
    model_name      VARCHAR(100) NOT NULL,
    is_custom_model BIT NOT NULL
);
GO

IF OBJECT_ID('dbo.dim_product') IS NULL
CREATE TABLE dbo.dim_product (
    product_key     BIGINT IDENTITY NOT NULL,
    product         VARCHAR(100) NOT NULL,
    sku             VARCHAR(200) NOT NULL,
    unit_type       VARCHAR(50)  NULL
);
GO

IF OBJECT_ID('dbo.dim_editor') IS NULL
CREATE TABLE dbo.dim_editor (
    editor_key      BIGINT IDENTITY NOT NULL,
    editor_name     VARCHAR(100) NOT NULL,   -- raw `ide` value, e.g. vscode
    editor_family   VARCHAR(50)  NULL              -- VS Code, Visual Studio, JetBrains, ...
);
GO

IF OBJECT_ID('dbo.dim_feature') IS NULL
CREATE TABLE dbo.dim_feature (
    feature_key     BIGINT IDENTITY NOT NULL,
    feature         VARCHAR(50) NOT NULL,   -- code_completion, chat_panel_agent_mode, ...
    feature_group   VARCHAR(50) NULL               -- Completions, Chat, Agent, CLI, App
);
GO

IF OBJECT_ID('dbo.dim_team') IS NULL
CREATE TABLE dbo.dim_team (
    team_key    BIGINT IDENTITY NOT NULL,
    team_id     BIGINT       NOT NULL,
    team_slug   VARCHAR(200) NOT NULL,
    org_key     BIGINT       NOT NULL
);
GO

------------------------------------------------------------------------- facts

-- Exact dollars, straight from the per-user premium request billing report.
IF OBJECT_ID('dbo.fact_premium_requests') IS NULL
CREATE TABLE dbo.fact_premium_requests (
    date_key        INT            NOT NULL,
    user_key        BIGINT         NOT NULL,
    org_key         BIGINT         NOT NULL,
    model_key       BIGINT         NOT NULL,
    product_key     BIGINT         NOT NULL,
    net_quantity    DECIMAL(18,4)  NOT NULL,
    gross_quantity  DECIMAL(18,4)  NOT NULL,
    price_per_unit  DECIMAL(18,6)  NOT NULL,
    gross_amount    DECIMAL(18,4)  NOT NULL,
    discount_amount DECIMAL(18,4)  NOT NULL,
    net_amount      DECIMAL(18,4)  NOT NULL,
    loaded_at       DATETIME2(0)   NOT NULL
);
GO

-- One row per user per day: headline activity, adoption phase, and credits consumed.
IF OBJECT_ID('dbo.fact_user_day') IS NULL
CREATE TABLE dbo.fact_user_day (
    date_key                       INT NOT NULL,
    user_key                       BIGINT NOT NULL,
    org_key                        BIGINT NOT NULL,
    ai_credits_used                DECIMAL(18,4) NOT NULL,
    adoption_phase                 VARCHAR(50) NULL,
    adoption_phase_number          INT NULL,
    used_agent                     BIT NULL,
    used_chat                      BIT NULL,
    used_cli                       BIT NULL,
    used_copilot_app               BIT NULL,
    used_copilot_cloud_agent       BIT NULL,
    used_code_review_active        BIT NULL,
    used_code_review_passive       BIT NULL,
    user_initiated_interaction_count INT NOT NULL,
    code_generation_activity_count INT NOT NULL,
    code_acceptance_activity_count INT NOT NULL,
    loc_suggested_to_add_sum       INT NOT NULL,
    loc_added_sum                  INT NOT NULL,
    loc_deleted_sum                INT NOT NULL,
    loaded_at                      DATETIME2(0) NOT NULL
);
GO

-- Per-user IDE activity. This is what makes editor attribution per-user rather than org-wide.
IF OBJECT_ID('dbo.fact_user_ide_day') IS NULL
CREATE TABLE dbo.fact_user_ide_day (
    date_key                       INT NOT NULL,
    user_key                       BIGINT NOT NULL,
    org_key                        BIGINT NOT NULL,
    editor_key                     BIGINT NOT NULL,
    last_known_ide_version         VARCHAR(100) NULL,
    last_known_plugin_version      VARCHAR(100) NULL,
    user_initiated_interaction_count INT NOT NULL,
    code_generation_activity_count INT NOT NULL,
    code_acceptance_activity_count INT NOT NULL,
    loc_added_sum                  INT NOT NULL,
    loc_deleted_sum                INT NOT NULL,
    loaded_at                      DATETIME2(0) NOT NULL
);
GO

-- Per-user model x feature activity. Chat activity only, per GitHub's documentation.
IF OBJECT_ID('dbo.fact_user_model_feature_day') IS NULL
CREATE TABLE dbo.fact_user_model_feature_day (
    date_key                       INT NOT NULL,
    user_key                       BIGINT NOT NULL,
    org_key                        BIGINT NOT NULL,
    model_key                      BIGINT NOT NULL,
    feature_key                    BIGINT NOT NULL,
    user_initiated_interaction_count INT NOT NULL,
    code_generation_activity_count INT NOT NULL,
    code_acceptance_activity_count INT NOT NULL,
    loaded_at                      DATETIME2(0) NOT NULL
);
GO

-- User-to-team bridge. GitHub omits teams with fewer than 5 seated users, so this
-- is deliberately incomplete; rollups must account for unmapped users.
IF OBJECT_ID('dbo.fact_user_team_day') IS NULL
CREATE TABLE dbo.fact_user_team_day (
    date_key  INT NOT NULL,
    user_key  BIGINT NOT NULL,
    team_key  BIGINT NOT NULL,
    loaded_at DATETIME2(0) NOT NULL
);
GO

/*
    Per-user allocation bridge.

    GitHub does not publish an ide x model cross-product, so editor attribution is
    still modelled - but now from the individual's own activity rather than an
    org-wide average. Assumption: P(ide | user, day, model) is approximated by
    P(ide | user, day).

    Shares sum to 1.0 within each (date, user, org).
*/
IF OBJECT_ID('dbo.fact_user_ide_share') IS NULL
CREATE TABLE dbo.fact_user_ide_share (
    date_key    INT NOT NULL,
    user_key    BIGINT NOT NULL,
    org_key     BIGINT NOT NULL,
    editor_key  BIGINT NOT NULL,
    share       DECIMAL(9,8) NOT NULL,
    weight_basis VARCHAR(40) NOT NULL,   -- which activity signal produced the weight
    loaded_at   DATETIME2(0) NOT NULL
);
GO

-- Ingestion audit: proves a run happened and how much it moved.
IF OBJECT_ID('dbo.ingestion_run') IS NULL
CREATE TABLE dbo.ingestion_run (
    run_id        VARCHAR(36) NOT NULL,
    started_at    DATETIME2(0) NOT NULL,
    completed_at  DATETIME2(0) NULL,
    since_date    DATE NULL,
    until_date    DATE NULL,
    trigger_source VARCHAR(30) NULL,
    status        VARCHAR(20) NOT NULL,
    rows_user_day INT NULL,
    rows_user_ide INT NULL,
    rows_model_feature INT NULL,
    rows_premium_requests INT NULL,
    rows_seats    INT NULL,
    message       VARCHAR(2000) NULL
);
GO

-- Fabric Warehouse automatically manages physical layout. PK/UNIQUE/FK metadata is
-- deliberately omitted because Fabric constraints are NOT ENFORCED; the idempotent
-- load procedures own uniqueness and referential integrity.
