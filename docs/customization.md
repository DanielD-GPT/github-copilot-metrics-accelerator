# Customization

## Cost centers

`dim_user.cost_center` ships empty. Populate it however your organization tracks chargeback:

```sql
-- Straight from GitHub team names
UPDATE dbo.dim_user SET cost_center = team_name WHERE cost_center IS NULL;

-- Or from an HR / CMDB mapping table you load separately
UPDATE u SET u.cost_center = m.cost_center
FROM dbo.dim_user u JOIN dbo.ref_user_costcenter m ON m.user_login = u.user_login;
```

## Allocation weighting

The editor split lives in `dbo.sp_build_user_ide_share`. To change how spend is attributed across
IDEs, edit the `CASE` expression that picks the weight signal.

**Example — weight by code generated rather than prompts sent:**

```sql
WHEN b.total_generations > 0 THEN CAST(s.generations AS DECIMAL(18,6)) / b.total_generations
```

**Example — drop the equal-split fallback** so weakly-evidenced spend stays unallocated rather than
being spread evenly:

```sql
ELSE NULL   -- instead of 1.0 / NULLIF(b.ide_count, 0)
```

The feature split is computed in `vw_spend_by_user_model_editor` from the user's own
`totals_by_model_feature`, weighted by `user_initiated_interaction_count`.

Re-run `EXEC dbo.sp_build_user_ide_share` afterwards to rebuild history.

## Improving attribution accuracy

Every weight already comes from the individual's own activity. The remaining gap is that GitHub
publishes IDE and model/feature breakdowns as sibling arrays, never as a cross-product.

1. **Current model** — assumes `P(ide | user, day, model) ≈ P(ide | user, day)`.
2. **Single-IDE users** — already exact: if a user only used VS Code that day, their share is 1.0.
3. **Client telemetry** — if you collect IDE telemetry with model context, join it in and replace
   the bridge. This is the only route to genuinely audit-grade per-surface attribution.

Monitor `Equal Split Fallback %` and `Unallocated %` in Power BI to see how much spend rests on
assumption rather than observation.

## Billing request volume

Per-user spend requires one request per user per period.

```bash
azd env set BILLING_GRANULARITY "month"   # ~30x fewer requests, monthly precision
azd env set MAX_BILLING_USERS   "500"     # hard cap per org
azd env set RELOAD_TRAILING_DAYS "3"      # narrower correction window
```

## Ingestion schedule

```bash
azd env set INGESTION_SCHEDULE "0 0 */6 * * *"   # every 6 hours
azd env set RELOAD_TRAILING_DAYS "14"            # wider correction window
azd up
```

The schedule is an NCRONTAB expression in UTC: `{second} {minute} {hour} {day} {month} {day-of-week}`.

## Adding a data source

1. Add a fetch method to `GitHubClient` in `src/functions/shared/github_client.py`.
2. Add a flattener in `shared/transform.py`.
3. Add a staging table in `sql/02_staging.sql` and register it in `STAGING_TABLES` in `sql_loader.py`.
4. Add a MERGE procedure and call it from `dbo.sp_load_all`.
5. Extend `sp_reconcile_load` so the new source fails loudly when it drops rows.
6. Archive the raw payload with `lake.write(...)` before parsing — always.

## Retention

Raw zone lifecycle rules live in `infra/core/storage.bicep`: cool at 90 days, archive at 365, never
delete. To add deletion after seven years:

```bicep
delete: {
  daysAfterModificationGreaterThan: 2555
}
```

## Scaling the database

```bicep
// infra/core/sql.bicep
param maxCapacity int = 8       // more vCores
param autoPauseDelay int = -1   // never pause (higher cost, no cold start)
```

## Restricting the backfill endpoint

The HTTP trigger uses `AuthLevel.FUNCTION`. To lock it down further, set
`authLevel` to `ADMIN`, or front it with Azure API Management or an App Service access restriction
limiting callers to your corporate IP range.
