# Architecture

## Data flow

1. **Resolve** — for each org and each day, call the report endpoints. They return
   `download_links` (pre-signed URLs), not data.
2. **Download** — fetch each link **without** an Authorization header (the URLs are already
   signed) and parse the NDJSON, transparently handling gzip.
3. **Land** — write every payload verbatim to ADLS Gen2 at
   `raw/{dataset}/dt=YYYY-MM-DD/...` **before** any parsing.
4. **Fan out billing** — for each licensed user, call the premium request usage endpoint with
   `user=`. This filter is the only thing that makes spend attributable.
5. **Stage** — staging tables are cleared and bulk inserted in a single transaction.
6. **Merge** — `dbo.sp_load_all` upserts dimensions, then facts, rebuilds the per-user IDE
   share, and finally runs `sp_reconcile_load`, which throws if rows failed to map.
7. **Serve** — views feed Power BI, the internal API, and the Teams bot.

## Why per-org, not per-enterprise

Enterprise-scope per-user records carry `organization_id` (a numeric ID) but no org login, while
the billing endpoint is addressed by org login. Pulling metrics per org is what lets the two join.
`GITHUB_ORGS` is therefore required and enforced by `Settings.validate()`.

## Why the raw zone still matters

Metrics reports retain roughly a year and billing 24 months, so GitHub is not about to drop your
history. The raw zone earns its place differently: it makes the pipeline **replayable**. Fix a
transform bug, re-run against the archive, rebuild — without re-paying the per-user billing
request cost or waiting on rate limits.

## Idempotency

Every load is safe to re-run:

- Staging is cleared per run, so no duplicates accumulate.
- Facts are loaded with `MERGE` on their full grain.
- `fact_user_ide_share` is deleted and rebuilt for the date range being processed.

The daily timer reloads a **trailing 7 days** rather than just yesterday, because GitHub posts
billing corrections several days late. Re-processing overwrites with corrected values.

> **No concurrency guard yet.** The timer and the backfill endpoint share staging tables. Running
> a backfill while the timer is mid-run can corrupt a load. Avoid overlapping runs until a
> singleton lock is added.

## The allocation bridge

```
allocated $ = exact $(user, day, model)
            × ide_share(user, day)
            × feature_share(user, day, model)
```

`ide_share` is built in `dbo.sp_build_user_ide_share` from that individual's own `totals_by_ide`.
The weight signal is chosen in preference order, recorded per row in `weight_basis`:

| Order | Signal | `weight_basis` |
|---|---|---|
| 1 | `user_initiated_interaction_count` — explicit prompts, closest proxy for a premium request | `user_interactions` |
| 2 | `code_generation_activity_count` — fallback when interactions are all zero | `code_generations` |
| 3 | Equal split across the user's IDEs — last resort so spend is never silently dropped | `equal_split` |

`feature_share` is computed in the view from that user's `totals_by_model_feature` for the same
model, weighted by interactions.

### Known limitations

1. **No IDE × model cross-product.** GitHub publishes `totals_by_ide` and `totals_by_model_feature`
   as sibling arrays. The bridge assumes `P(ide | user, day, model) ≈ P(ide | user, day)`. A
   developer who uses GPT-5.3 exclusively in Visual Studio but writes most of their code in VS Code
   will have that model's spend skewed toward VS Code.
2. **`model` can be `auto`.** When auto-selection ran and no specific model was attributed, GitHub
   reports `auto`. Surface it as its own bucket rather than hiding it.
3. **`totals_by_model_feature` is chat activity only**, not completions. Completion-heavy users may
   show little feature detail.
4. **Teams under 5 seats are omitted** from the user-teams report, so team rollups will not
   reconcile to enterprise totals. Use `vw_spend_by_user_model` for a complete figure.
5. **`equal_split` rows are weakly evidenced.** Track `Equal Split Fallback %` in Power BI.

## Cost of the billing fan-out

The billing API attributes spend only when filtered per user, so request volume is
`users × periods`. A 500-seat org over a 7-day window is ~3,500 requests per run.

| Setting | Effect |
|---|---|
| `BILLING_GRANULARITY=day` | Accurate daily spend; highest request volume |
| `BILLING_GRANULARITY=month` | ~30× fewer requests; spend lands on the first of the month |
| `MAX_BILLING_USERS` | Hard cap per org; logs a warning when it truncates |

## Failing loudly

`sp_reconcile_load` throws when:

- Billing rows exist in staging that could not be mapped to dimensions (`51001`)
- Neither metrics nor billing produced any rows at all (`51002`)

Every run writes to `dbo.ingestion_run` with row counts and status, and `/api/health` reports the
last **successful** run. Set `FAIL_ON_EMPTY_REPORT=false` to downgrade these to warnings.

## Security

| Control | Implementation |
|---|---|
| No secrets in code | GitHub credential lives only in Key Vault |
| No SQL passwords | `azureADOnlyAuthentication: true`; the Bicep contains no admin login |
| Passwordless Azure access | System-assigned managed identity + RBAC |
| Least privilege | Key Vault **Secrets User**, Storage **Blob Data Contributor**, SQL `db_datareader`/`db_datawriter`/`EXECUTE` |
| No schema-modification grant | Staging is cleared with `DELETE`, so no `ALTER ON SCHEMA` is needed |
| Transport | HTTPS only, TLS 1.2 minimum, FTPS disabled |
| Storage | Shared key access disabled, public blob access disabled |
| Error handling | API responses return generic messages; detail goes to Application Insights only |

### Not yet addressed

- **No per-caller authorization.** Any holder of the function key can query any individual's spend.
- **Row-level security is deployed but disabled.** Turning it on is one statement, and deliberately
  left to the customer so a first deployment succeeds without access mapping.
- **Public network access is enabled** on SQL, Storage, and Key Vault for first-run simplicity.
  Production should add Private Endpoints and set `defaultAction: 'Deny'`.

## Privacy

This pipeline collects **per-developer** activity: IDE names and versions, lines of code added and
deleted, interaction counts, and dollar spend, all keyed to a GitHub login.

Before deploying, settle:

- The lawful basis for processing, and whether a DPIA is required
- Works council or employee representative consultation, where applicable
- Who may see individual-level rows. Row-level security is deployed but **disabled by default** so
  first deployments work out of the box; enable it in `sql/06_security.sql` before sharing reports
  beyond the project team
- A retention and deletion policy; `dbo.sp_purge_personal_data` and `rawRetentionDays` implement one

Using this data for individual performance management is a decision to make deliberately and
document, not one to arrive at by accident because the dashboard made it easy.

## Scaling notes

- Metrics volume scales with orgs × days; billing scales with users × periods. Billing dominates.
- The 30-minute `functionTimeout` bounds a single run. Large tenants should reduce
  `RELOAD_TRAILING_DAYS`, switch to monthly billing granularity, or split per-org onto a queue.
- All datasets accumulate in memory before the load. Very large tenants should stream per-org.
