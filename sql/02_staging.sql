/*
    02_staging.sql — landing tables written by the Function App.
    These are truncated and repopulated on every run; they hold no history.
*/

IF OBJECT_ID('stg.user_day') IS NULL
CREATE TABLE stg.user_day (
    activity_date                  DATE          NOT NULL,
    user_login                     VARCHAR(100)  NOT NULL,
    user_id                        BIGINT        NULL,
    org_login                      VARCHAR(100)  NOT NULL,
    organization_id                VARCHAR(50)   NULL,
    enterprise_id                  VARCHAR(50)   NULL,
    ai_credits_used                DECIMAL(18,4) NULL,
    adoption_phase                 VARCHAR(50)   NULL,
    adoption_phase_number          INT           NULL,
    used_agent                     BIT           NULL,
    used_chat                      BIT           NULL,
    used_cli                       BIT           NULL,
    used_copilot_app               BIT           NULL,
    used_copilot_cloud_agent       BIT           NULL,
    used_code_review_active        BIT           NULL,
    used_code_review_passive       BIT           NULL,
    user_initiated_interaction_count INT         NOT NULL,
    code_generation_activity_count INT           NOT NULL,
    code_acceptance_activity_count INT           NOT NULL,
    loc_suggested_to_add_sum       INT           NOT NULL,
    loc_suggested_to_delete_sum    INT           NOT NULL,
    loc_added_sum                  INT           NOT NULL,
    loc_deleted_sum                INT           NOT NULL
);
GO

IF OBJECT_ID('stg.user_ide') IS NULL
CREATE TABLE stg.user_ide (
    activity_date                  DATE          NOT NULL,
    user_login                     VARCHAR(100)  NOT NULL,
    org_login                      VARCHAR(100)  NOT NULL,
    ide                            VARCHAR(100)  NOT NULL,
    ide_family                     VARCHAR(50)   NOT NULL,
    last_known_ide_version         VARCHAR(100)  NULL,
    last_known_plugin_version      VARCHAR(100)  NULL,
    user_initiated_interaction_count INT         NOT NULL,
    code_generation_activity_count INT           NOT NULL,
    code_acceptance_activity_count INT           NOT NULL,
    loc_suggested_to_add_sum       INT           NOT NULL,
    loc_suggested_to_delete_sum    INT           NOT NULL,
    loc_added_sum                  INT           NOT NULL,
    loc_deleted_sum                INT           NOT NULL
);
GO

IF OBJECT_ID('stg.user_model_feature') IS NULL
CREATE TABLE stg.user_model_feature (
    activity_date                  DATE          NOT NULL,
    user_login                     VARCHAR(100)  NOT NULL,
    org_login                      VARCHAR(100)  NOT NULL,
    model_name                     VARCHAR(100)  NOT NULL,
    feature                        VARCHAR(50)   NOT NULL,
    user_initiated_interaction_count INT         NOT NULL,
    code_generation_activity_count INT           NOT NULL,
    code_acceptance_activity_count INT           NOT NULL,
    loc_suggested_to_add_sum       INT           NOT NULL,
    loc_suggested_to_delete_sum    INT           NOT NULL,
    loc_added_sum                  INT           NOT NULL,
    loc_deleted_sum                INT           NOT NULL
);
GO

IF OBJECT_ID('stg.user_teams') IS NULL
CREATE TABLE stg.user_teams (
    activity_date DATE         NOT NULL,
    user_login    VARCHAR(100) NOT NULL,
    user_id       BIGINT       NULL,
    org_login     VARCHAR(100) NOT NULL,
    team_id       BIGINT       NOT NULL,
    team_slug     VARCHAR(200) NOT NULL
);
GO

IF OBJECT_ID('stg.premium_requests') IS NULL
CREATE TABLE stg.premium_requests (
    usage_date        DATE          NOT NULL,
    user_login        VARCHAR(100)  NOT NULL,
    org_login         VARCHAR(100)  NOT NULL,
    product           VARCHAR(100)  NULL,
    sku               VARCHAR(200)  NULL,
    model_name        VARCHAR(100)  NULL,
    unit_type         VARCHAR(50)   NULL,
    price_per_unit    DECIMAL(18,6) NULL,
    gross_quantity    DECIMAL(18,4) NULL,
    discount_quantity DECIMAL(18,4) NULL,
    net_quantity      DECIMAL(18,4) NULL,
    gross_amount      DECIMAL(18,4) NULL,
    discount_amount   DECIMAL(18,4) NULL,
    net_amount        DECIMAL(18,4) NULL
);
GO

IF OBJECT_ID('stg.seats') IS NULL
CREATE TABLE stg.seats (
    user_login                VARCHAR(100) NOT NULL,
    user_id                   BIGINT       NULL,
    org_login                 VARCHAR(100) NOT NULL,
    team_name                 VARCHAR(200) NULL,
    plan_type                 VARCHAR(50)  NULL,
    created_at                DATETIME2(0) NULL,
    last_activity_at          DATETIME2(0) NULL,
    last_activity_editor      VARCHAR(100) NULL,
    pending_cancellation_date DATE         NULL
);
GO
