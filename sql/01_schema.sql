/*
    01_schema.sql — dimensional model
    Run order: 01_schema -> 02_staging -> 03_procedures -> 04_views
*/

IF SCHEMA_ID('stg') IS NULL EXEC('CREATE SCHEMA stg');
GO

-------------------------------------------------------------------- dimensions

IF OBJECT_ID('dbo.dim_date') IS NULL
CREATE TABLE dbo.dim_date (
    date_key        INT          NOT NULL PRIMARY KEY,   -- yyyymmdd
    [date]          DATE         NOT NULL UNIQUE,
    [year]          SMALLINT     NOT NULL,
    [month]         TINYINT      NOT NULL,
    [day]           TINYINT      NOT NULL,
    month_name      VARCHAR(20)  NOT NULL,
    year_month      CHAR(7)      NOT NULL,
    day_of_week     TINYINT      NOT NULL,
    is_weekend      BIT          NOT NULL
);
GO

IF OBJECT_ID('dbo.dim_org') IS NULL
CREATE TABLE dbo.dim_org (
    org_key         INT IDENTITY(1,1) PRIMARY KEY,
    org_login       VARCHAR(100) NOT NULL UNIQUE
);
GO

IF OBJECT_ID('dbo.dim_user') IS NULL
CREATE TABLE dbo.dim_user (
    user_key        INT IDENTITY(1,1) PRIMARY KEY,
    user_login      VARCHAR(100) NOT NULL UNIQUE,
    github_user_id  BIGINT       NULL,
    team_name       VARCHAR(200) NULL,
    cost_center     VARCHAR(200) NULL,   -- populated by the customer
    plan_type       VARCHAR(50)  NULL,
    seat_created_at DATETIME2(0) NULL,
    last_activity_at DATETIME2(0) NULL,
    last_activity_editor VARCHAR(100) NULL,
    is_active       BIT NOT NULL DEFAULT 1
);
GO

IF OBJECT_ID('dbo.dim_model') IS NULL
CREATE TABLE dbo.dim_model (
    model_key       INT IDENTITY(1,1) PRIMARY KEY,
    model_name      VARCHAR(100) NOT NULL UNIQUE,
    is_custom_model BIT NOT NULL DEFAULT 0
);
GO

IF OBJECT_ID('dbo.dim_product') IS NULL
CREATE TABLE dbo.dim_product (
    product_key     INT IDENTITY(1,1) PRIMARY KEY,
    product         VARCHAR(100) NOT NULL,
    sku             VARCHAR(200) NOT NULL,
    unit_type       VARCHAR(50)  NULL,
    CONSTRAINT uq_dim_product UNIQUE (product, sku)
);
GO

IF OBJECT_ID('dbo.dim_editor') IS NULL
CREATE TABLE dbo.dim_editor (
    editor_key      INT IDENTITY(1,1) PRIMARY KEY,
    editor_name     VARCHAR(100) NOT NULL UNIQUE,   -- raw `ide` value, e.g. vscode
    editor_family   VARCHAR(50)  NULL              -- VS Code, Visual Studio, JetBrains, ...
);
GO

IF OBJECT_ID('dbo.dim_feature') IS NULL
CREATE TABLE dbo.dim_feature (
    feature_key     INT IDENTITY(1,1) PRIMARY KEY,
    feature         VARCHAR(50) NOT NULL UNIQUE,   -- code_completion, chat_panel_agent_mode, ...
    feature_group   VARCHAR(50) NULL               -- Completions, Chat, Agent, CLI, App
);
GO

IF OBJECT_ID('dbo.dim_team') IS NULL
CREATE TABLE dbo.dim_team (
    team_key    INT IDENTITY(1,1) PRIMARY KEY,
    team_id     BIGINT       NOT NULL,
    team_slug   VARCHAR(200) NOT NULL,
    org_key     INT          NOT NULL,
    CONSTRAINT uq_dim_team UNIQUE (team_id, org_key),
    CONSTRAINT fk_dim_team_org FOREIGN KEY (org_key) REFERENCES dbo.dim_org(org_key)
);
GO

------------------------------------------------------------------------- facts

-- Exact dollars, straight from the per-user premium request billing report.
IF OBJECT_ID('dbo.fact_premium_requests') IS NULL
CREATE TABLE dbo.fact_premium_requests (
    date_key        INT            NOT NULL,
    user_key        INT            NOT NULL,
    org_key         INT            NOT NULL,
    model_key       INT            NOT NULL,
    product_key     INT            NOT NULL,
    net_quantity    DECIMAL(18,4)  NOT NULL DEFAULT 0,
    gross_quantity  DECIMAL(18,4)  NOT NULL DEFAULT 0,
    price_per_unit  DECIMAL(18,6)  NOT NULL DEFAULT 0,
    gross_amount    DECIMAL(18,4)  NOT NULL DEFAULT 0,
    discount_amount DECIMAL(18,4)  NOT NULL DEFAULT 0,
    net_amount      DECIMAL(18,4)  NOT NULL DEFAULT 0,
    loaded_at       DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_fact_premium_requests
        PRIMARY KEY (date_key, user_key, org_key, model_key, product_key),
    CONSTRAINT fk_fpr_date    FOREIGN KEY (date_key)    REFERENCES dbo.dim_date(date_key),
    CONSTRAINT fk_fpr_user    FOREIGN KEY (user_key)    REFERENCES dbo.dim_user(user_key),
    CONSTRAINT fk_fpr_org     FOREIGN KEY (org_key)     REFERENCES dbo.dim_org(org_key),
    CONSTRAINT fk_fpr_model   FOREIGN KEY (model_key)   REFERENCES dbo.dim_model(model_key),
    CONSTRAINT fk_fpr_product FOREIGN KEY (product_key) REFERENCES dbo.dim_product(product_key)
);
GO

-- One row per user per day: headline activity, adoption phase, and credits consumed.
IF OBJECT_ID('dbo.fact_user_day') IS NULL
CREATE TABLE dbo.fact_user_day (
    date_key                       INT NOT NULL,
    user_key                       INT NOT NULL,
    org_key                        INT NOT NULL,
    ai_credits_used                DECIMAL(18,4) NOT NULL DEFAULT 0,
    adoption_phase                 VARCHAR(50) NULL,
    adoption_phase_number          INT NULL,
    used_agent                     BIT NULL,
    used_chat                      BIT NULL,
    used_cli                       BIT NULL,
    used_copilot_app               BIT NULL,
    used_copilot_cloud_agent       BIT NULL,
    used_code_review_active        BIT NULL,
    used_code_review_passive       BIT NULL,
    user_initiated_interaction_count INT NOT NULL DEFAULT 0,
    code_generation_activity_count INT NOT NULL DEFAULT 0,
    code_acceptance_activity_count INT NOT NULL DEFAULT 0,
    loc_suggested_to_add_sum       INT NOT NULL DEFAULT 0,
    loc_added_sum                  INT NOT NULL DEFAULT 0,
    loc_deleted_sum                INT NOT NULL DEFAULT 0,
    loaded_at                      DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_fact_user_day PRIMARY KEY (date_key, user_key, org_key),
    CONSTRAINT fk_fud_date FOREIGN KEY (date_key) REFERENCES dbo.dim_date(date_key),
    CONSTRAINT fk_fud_user FOREIGN KEY (user_key) REFERENCES dbo.dim_user(user_key),
    CONSTRAINT fk_fud_org  FOREIGN KEY (org_key)  REFERENCES dbo.dim_org(org_key)
);
GO

-- Per-user IDE activity. This is what makes editor attribution per-user rather than org-wide.
IF OBJECT_ID('dbo.fact_user_ide_day') IS NULL
CREATE TABLE dbo.fact_user_ide_day (
    date_key                       INT NOT NULL,
    user_key                       INT NOT NULL,
    org_key                        INT NOT NULL,
    editor_key                     INT NOT NULL,
    last_known_ide_version         VARCHAR(100) NULL,
    last_known_plugin_version      VARCHAR(100) NULL,
    user_initiated_interaction_count INT NOT NULL DEFAULT 0,
    code_generation_activity_count INT NOT NULL DEFAULT 0,
    code_acceptance_activity_count INT NOT NULL DEFAULT 0,
    loc_added_sum                  INT NOT NULL DEFAULT 0,
    loc_deleted_sum                INT NOT NULL DEFAULT 0,
    loaded_at                      DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_fact_user_ide_day PRIMARY KEY (date_key, user_key, org_key, editor_key),
    CONSTRAINT fk_fuid_date   FOREIGN KEY (date_key)   REFERENCES dbo.dim_date(date_key),
    CONSTRAINT fk_fuid_user   FOREIGN KEY (user_key)   REFERENCES dbo.dim_user(user_key),
    CONSTRAINT fk_fuid_org    FOREIGN KEY (org_key)    REFERENCES dbo.dim_org(org_key),
    CONSTRAINT fk_fuid_editor FOREIGN KEY (editor_key) REFERENCES dbo.dim_editor(editor_key)
);
GO

-- Per-user model x feature activity. Chat activity only, per GitHub's documentation.
IF OBJECT_ID('dbo.fact_user_model_feature_day') IS NULL
CREATE TABLE dbo.fact_user_model_feature_day (
    date_key                       INT NOT NULL,
    user_key                       INT NOT NULL,
    org_key                        INT NOT NULL,
    model_key                      INT NOT NULL,
    feature_key                    INT NOT NULL,
    user_initiated_interaction_count INT NOT NULL DEFAULT 0,
    code_generation_activity_count INT NOT NULL DEFAULT 0,
    code_acceptance_activity_count INT NOT NULL DEFAULT 0,
    loaded_at                      DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_fact_user_model_feature_day
        PRIMARY KEY (date_key, user_key, org_key, model_key, feature_key),
    CONSTRAINT fk_fumf_date    FOREIGN KEY (date_key)    REFERENCES dbo.dim_date(date_key),
    CONSTRAINT fk_fumf_user    FOREIGN KEY (user_key)    REFERENCES dbo.dim_user(user_key),
    CONSTRAINT fk_fumf_org     FOREIGN KEY (org_key)     REFERENCES dbo.dim_org(org_key),
    CONSTRAINT fk_fumf_model   FOREIGN KEY (model_key)   REFERENCES dbo.dim_model(model_key),
    CONSTRAINT fk_fumf_feature FOREIGN KEY (feature_key) REFERENCES dbo.dim_feature(feature_key)
);
GO

-- User-to-team bridge. GitHub omits teams with fewer than 5 seated users, so this
-- is deliberately incomplete; rollups must account for unmapped users.
IF OBJECT_ID('dbo.fact_user_team_day') IS NULL
CREATE TABLE dbo.fact_user_team_day (
    date_key  INT NOT NULL,
    user_key  INT NOT NULL,
    team_key  INT NOT NULL,
    loaded_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_fact_user_team_day PRIMARY KEY (date_key, user_key, team_key),
    CONSTRAINT fk_futd_date FOREIGN KEY (date_key) REFERENCES dbo.dim_date(date_key),
    CONSTRAINT fk_futd_user FOREIGN KEY (user_key) REFERENCES dbo.dim_user(user_key),
    CONSTRAINT fk_futd_team FOREIGN KEY (team_key) REFERENCES dbo.dim_team(team_key)
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
    user_key    INT NOT NULL,
    org_key     INT NOT NULL,
    editor_key  INT NOT NULL,
    share       DECIMAL(9,8) NOT NULL,
    weight_basis VARCHAR(40) NOT NULL,   -- which activity signal produced the weight
    loaded_at   DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_fact_user_ide_share PRIMARY KEY (date_key, user_key, org_key, editor_key),
    CONSTRAINT ck_user_share_range CHECK (share >= 0 AND share <= 1)
);
GO

-- Ingestion audit: proves a run happened and how much it moved.
IF OBJECT_ID('dbo.ingestion_run') IS NULL
CREATE TABLE dbo.ingestion_run (
    run_id        BIGINT IDENTITY(1,1) PRIMARY KEY,
    started_at    DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    completed_at  DATETIME2(0) NULL,
    since_date    DATE NULL,
    until_date    DATE NULL,
    trigger_source VARCHAR(30) NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'running',
    rows_user_day INT NULL,
    rows_user_ide INT NULL,
    rows_model_feature INT NULL,
    rows_premium_requests INT NULL,
    rows_seats    INT NULL,
    message       NVARCHAR(2000) NULL
);
GO

CREATE NONCLUSTERED INDEX ix_fpr_user_date
    ON dbo.fact_premium_requests (user_key, date_key) INCLUDE (net_amount, model_key);
GO

CREATE NONCLUSTERED INDEX ix_fuis_lookup
    ON dbo.fact_user_ide_share (date_key, user_key, org_key) INCLUDE (editor_key, share);
GO

CREATE NONCLUSTERED INDEX ix_fumf_lookup
    ON dbo.fact_user_model_feature_day (date_key, user_key, org_key, model_key)
    INCLUDE (feature_key, user_initiated_interaction_count);
GO
