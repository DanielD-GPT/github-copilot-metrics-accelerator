// Power Query (M) source scripts.
// Paste each block into Power BI Desktop: Home > Transform data > New Source > Blank Query,
// then Advanced Editor. Set SERVER and DATABASE once in the parameters below.
//
// Use DirectQuery for always-current data, or Import with a scheduled refresh after
// 03:00 UTC (the ingestion timer runs at 02:00 UTC). Both read the Fabric Warehouse
// SQL endpoint and consume Fabric capacity units when they query.

// ---------------------------------------------------------------- parameters
// Create these two as Text parameters named exactly SqlServer and SqlDatabase.
// SqlServer   = FABRIC_SQL_ENDPOINT,   e.g. abc123.datawarehouse.fabric.microsoft.com
// SqlDatabase = FABRIC_WAREHOUSE_NAME, e.g. copilotmetrics

// ------------------------------------------------------- Spend (allocated)
let
    Source = Sql.Database(SqlServer, SqlDatabase),
    Data   = Source{[Schema="dbo", Item="vw_spend_by_user_model_editor"]}[Data]
in
    Data

// ------------------------------------------------------------ Spend (exact)
let
    Source = Sql.Database(SqlServer, SqlDatabase),
    Data   = Source{[Schema="dbo", Item="vw_spend_by_user_model"]}[Data]
in
    Data

// --------------------------------------------------------------- Editor mix
let
    Source = Sql.Database(SqlServer, SqlDatabase),
    Data   = Source{[Schema="dbo", Item="vw_editor_mix"]}[Data]
in
    Data

// -------------------------------------------------------- Model feature mix
let
    Source = Sql.Database(SqlServer, SqlDatabase),
    Data   = Source{[Schema="dbo", Item="vw_model_feature_mix"]}[Data]
in
    Data

// -------------------------------------------------------------- Team spend
// Sourced from GitHub's user-teams report. Teams with fewer than 5 seated users
// are omitted by GitHub, so this will not reconcile to total enterprise spend.
let
    Source = Sql.Database(SqlServer, SqlDatabase),
    Data   = Source{[Schema="dbo", Item="vw_spend_by_team"]}[Data]
in
    Data

// ------------------------------------------------------------ Team rollup
let
    Source = Sql.Database(SqlServer, SqlDatabase),
    Data   = Source{[Schema="dbo", Item="vw_monthly_spend_by_team"]}[Data]
in
    Data

// -------------------------------------------------------------- Idle seats
let
    Source = Sql.Database(SqlServer, SqlDatabase),
    Data   = Source{[Schema="dbo", Item="vw_inactive_seats"]}[Data]
in
    Data

// --------------------------------------------------------------- Date table
// Marked as the model's date table. Covers only dates present in the facts.
let
    Source    = Sql.Database(SqlServer, SqlDatabase),
    Data      = Source{[Schema="dbo", Item="dim_date"]}[Data],
    Typed     = Table.TransformColumnTypes(Data, {{"date", type date}}),
    Sorted    = Table.Sort(Typed, {{"date", Order.Ascending}})
in
    Sorted
