# Deployment Plan — GitHub Copilot Metrics & Spend Accelerator

**Status:** Draft — awaiting user approval
**Mode:** NEW (greenfield accelerator repo)
**Recipe:** AZD + Bicep
**Last updated:** 2026-09-01

---

## 1. Goal

Give customers granular, per-user visibility into GitHub Copilot consumption and spend, at a
finer grain than the native GitHub reports, e.g.:

> "Jimmy used $50 of GPT-5.3 in VS Code chat, $30 of GPT-5.6 in VS Code agents, and $20 of GPT-5.4 in Visual Studio."

## 2. Source systems (GitHub)

| Source | Endpoint | Why required | Key constraint |
|---|---|---|---|
| Copilot Metrics API | `/enterprises/{ent}/copilot/metrics` | Only source with **editor + feature + model** breakdown | 28-day rolling window → must ingest at least weekly |
| Enhanced Billing API | `/enterprises/{ent}/settings/billing/usage` | Only source with **USD per premium request** | Billing corrections backfill → re-load trailing 7 days |
| Seats / User Mgmt API | `/orgs/{org}/copilot/billing/seats` | Maps login → team → cost center | Per-org, must loop all orgs |

## 3. Target architecture (Azure)

| Component | SKU / Plan | Purpose |
|---|---|---|
| Azure Function App | Python 3.11, **Flex Consumption (FC1)** | Timer-triggered daily pull + HTTP backfill; handles pagination, 202 polling, retries |
| Azure Data Lake Storage Gen2 | Standard_LRS, HNS enabled | Immutable raw zone `dt=YYYY-MM-DD`; replay source because GitHub only retains 28 days |
| Azure SQL Database | **GP_S_Gen5** serverless, auto-pause | Star schema + allocation logic in T-SQL |
| Azure Key Vault | Standard, RBAC | GitHub App private key / PAT |
| Managed Identity | System-assigned on Function | Passwordless → Key Vault, Storage, SQL |
| App Insights + Log Analytics | Pay-as-you-go | Ingestion telemetry and failure alerts |

## 4. Data model (star schema)

- **Dimensions:** `dim_date`, `dim_user`, `dim_org`, `dim_model`, `dim_product`, `dim_editor`
- **Facts:** `fact_premium_requests` ($), `fact_engagement_daily` (activity), `fact_editor_share` (allocation bridge)
- **View:** `vw_spend_by_user_model_editor` — drives Power BI and the internal API

## 5. The allocation bridge — accuracy disclosure

GitHub does **not** tag each billed premium request with the editor that produced it. The
Copilot Metrics API reports engagement **in aggregate at the org/enterprise level**, not per user.

Therefore the accelerator:
1. Computes a daily editor/feature share from the aggregate Metrics API.
2. Multiplies that share against each user's actual billed spend.

This yields **modeled attribution, not audit-grade attribution**. Every report surface must
carry this disclaimer. Per-user and per-model dollars come straight from billing and *are* exact;
only the editor/surface split is modeled.

## 6. Security decisions

- Entra-only auth on Azure SQL (`azureADOnlyAuthentication: true`) — no SQL logins, no passwords in Bicep.
- Storage: `allowSharedKeyAccess: false`, `allowBlobPublicAccess: false`, TLS 1.2 minimum.
- Key Vault: RBAC authorization, soft-delete + purge protection.
- Function → all resources via system-assigned managed identity and RBAC role assignments.
- No secrets in source control; GitHub credential lives only in Key Vault.

## 7. Artifacts to generate

| Artifact | Location |
|---|---|
| azd config | `azure.yaml` |
| Infrastructure | `infra/` (`main.bicep` + `core/` modules) |
| Function app | `src/functions/` |
| Database objects | `sql/` (schema, staging, procs, views) |
| CI/CD | `.github/workflows/deploy.yml` |
| Docs | `docs/` + `README.md` |

## 8. Deployment steps

1. `azd auth login`
2. `azd env new <env>` and set `GITHUB_ENTERPRISE`, `GITHUB_ORGS`
3. Set `AZURE_PRINCIPAL_ID` / `AZURE_PRINCIPAL_NAME` (Entra admin for SQL)
4. `azd up`
5. Store GitHub credential in Key Vault
6. Apply `sql/*.sql` in order
7. Trigger backfill endpoint to seed history

---

**Next:** Present plan → on approval, generate artifacts → `azure-validate` → `azure-deploy`.
