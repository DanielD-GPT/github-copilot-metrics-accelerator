# Operations

## Daily rhythm

| Time (UTC) | What happens |
|---|---|
| 02:00 | Timer fires, reloads a trailing 7-day window |
| ~02:05 | Raw payloads archived, staging loaded, `sp_load_all` runs |
| 03:00+ | Safe window for Power BI scheduled refresh |

## Health checks

```kusto
// Ingestion failures in the last 7 days
traces
| where timestamp > ago(7d)
| where message has "Ingestion complete" or severityLevel >= 3
| project timestamp, message, severityLevel
| order by timestamp desc
```

```sql
-- Did last night's load land?
SELECT TOP 5 run_id, completed_at, since_date, until_date, trigger_source, status,
       rows_user_day, rows_user_ide, rows_premium_requests, message
FROM dbo.ingestion_run
ORDER BY run_id DESC;
```

```sql
-- Freshness by fact
SELECT 'premium_requests' AS fact, MAX(d.[date]) AS latest_date
FROM dbo.fact_premium_requests f JOIN dbo.dim_date d ON d.date_key = f.date_key
UNION ALL
SELECT 'user_day', MAX(d.[date])
FROM dbo.fact_user_day f JOIN dbo.dim_date d ON d.date_key = f.date_key
UNION ALL
SELECT 'user_ide', MAX(d.[date])
FROM dbo.fact_user_ide_day f JOIN dbo.dim_date d ON d.date_key = f.date_key;
```

```sql
-- How much spend rests on a weak editor weight?
SELECT weight_basis, COUNT(*) AS rows
FROM dbo.fact_user_ide_share
GROUP BY weight_basis;
```

## Recommended alert

Fire when no successful ingestion has been recorded in 36 hours:

```bash
az monitor scheduled-query create \
  --name copilot-ingestion-stalled \
  --resource-group "$(azd env get-value AZURE_RESOURCE_GROUP)" \
  --scopes "<application-insights-resource-id>" \
  --condition "count 'requests | where name == \"ingest_daily\" and success == true' < 1" \
  --window-size 36h --evaluation-frequency 6h
```

## Manual backfill

```bash
FUNC=$(azd env get-value FUNCTION_APP_NAME)
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
KEY=$(az functionapp keys list -g "$RG" -n "$FUNC" --query functionKeys.default -o tsv)

curl -X POST "https://$FUNC.azurewebsites.net/api/backfill?code=$KEY" \
  -H 'Content-Type: application/json' \
  -d '{"since":"2026-08-01","until":"2026-08-28"}'
```

Requests beyond GitHub's metrics retention (reports start 10 October 2025, roughly a year of
history) return billing only — activity will be empty for those dates, so spend lands as
`exact-unallocated`.

> Do not run a backfill while the daily timer is running. They share staging tables and there is
> no concurrency guard yet.

## Rebuilding from the raw zone

Because every payload is archived, you can rebuild the warehouse without calling GitHub:

1. Fix the transform or procedure.
2. Re-apply `sql/03_procedures.sql`.
3. Replay archived files from `raw/` through the staging tables.
4. Run `EXEC dbo.sp_load_all`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Login failed for user '<token-identified principal>'` | Managed identity has no database user | Run `sql/05_grants.sql` with the Function App name |
| Error 51001 on load | Billing rows could not map to dimensions | Inspect `stg.premium_requests` for blank `product` or `org_login` |
| Error 51002 on load | Extract returned nothing at all | Run `scripts/validate_github_api.py`; check the metrics policy is enabled |
| `200 OK but no download_links` | Report not generated for that day, or policy disabled | Try an earlier `--day`; confirm *Copilot usage metrics* is *Enabled everywhere* |
| Report download 403 | Signed URL expired or an auth header was sent | Links are short-lived; re-resolve them rather than caching |
| 403 with `X-RateLimit-Remaining: 0` | Billing fan-out exhausted the hourly limit | Set `BILLING_GRANULARITY=month`, lower `MAX_BILLING_USERS`, or use a GitHub App |
| Run exceeds 30 minutes | Too many users × days | Reduce `RELOAD_TRAILING_DAYS` or switch to monthly billing |
| All spend shows one editor | User genuinely used one IDE, or `weight_basis = equal_split` | Check `fact_user_ide_share.weight_basis` |
| Team totals look low | GitHub omits teams under 5 seated users | Expected; use `vw_spend_by_user_model` for complete totals |
| `Data source name not found` | ODBC driver missing locally | Install ODBC Driver 18; it is preinstalled on the Functions Linux image |
| SQL connection times out on first call | Serverless database resuming | Connection timeout is 60s; retry succeeds |

## Cost control

```bash
# Pause ingestion without tearing anything down
az functionapp stop -g "$(azd env get-value AZURE_RESOURCE_GROUP)" -n "$(azd env get-value FUNCTION_APP_NAME)"
```

The SQL database auto-pauses after 60 minutes idle. Frequent Power BI DirectQuery traffic keeps it
awake — switch to Import mode if the compute bill climbs.

## Teardown

```bash
azd down --purge
```

`--purge` is required because Key Vault has purge protection enabled.
