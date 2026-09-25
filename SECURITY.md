# Security

## Reporting a vulnerability

Please do **not** open a public issue for security vulnerabilities.

Report them privately through [GitHub Security Advisories](../../security/advisories/new), or contact
the repository maintainers directly. Include reproduction steps and the affected component, and you
can expect an acknowledgement while the issue is triaged.

## Security model

This accelerator is designed to hold no secrets in source control:

| Control | Implementation |
|---|---|
| GitHub credential | Stored in Azure Key Vault, read at runtime by managed identity |
| Fabric Warehouse | Function uses its system-assigned managed identity; no SQL password exists |
| Storage | Shared key access disabled, public blob access disabled, TLS 1.2 minimum |
| Function to Azure | System-assigned managed identity with least-privilege RBAC |
| API errors | Generic messages to callers; detail goes to Application Insights only |
| Teams webhook | HMAC-SHA256 signature verified with a constant-time comparison |

## Known limitations

These are **documented gaps**, not oversights. Review them before production use.

1. **No per-caller authorization.** Any holder of the function key can query any individual's
   spend through the REST API, and any member of the Teams channel can query any user. Add API
   Management, IP restrictions, or caller-identity checks before exposing this broadly.

2. **Public network access is enabled** on Storage and Key Vault so a first deployment succeeds
   without network plumbing. Fabric Warehouse requires outbound TDS access on TCP 1433.

3. **The Teams route is anonymous at the platform level.** Teams cannot send a function key, so
   authentication is the HMAC signature alone. Requests with an invalid signature are rejected
   before any query runs.

4. **Function keys do not rotate automatically.** Rotate them on your own schedule.

5. **Row-level security is off by default.** Anyone with Warehouse read access sees every
   developer's activity and spend. Enabling RLS is a single statement; see
   [Handling personal data](#handling-personal-data).

## Handling personal data

This warehouse stores per-developer activity and spend keyed to a GitHub login, including IDE
names and versions, lines of code added and deleted, and dollar amounts.

- Row-level security scaffolding is deployed but **disabled by default**, so a first deployment
  works without access plumbing. Enable it before sharing reports beyond the project team —
  see `sql/06_security.sql`.
- `dbo.sp_forget_user` handles erasure requests.
- `dbo.sp_purge_personal_data` enforces a retention window.
- The raw zone deletes payloads after `rawRetentionDays` (default 730).

Confirm your lawful basis for processing, and complete any required DPIA or works council
consultation, before deploying. See [docs/architecture.md](docs/architecture.md#privacy).
