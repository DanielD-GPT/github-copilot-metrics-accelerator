# Support

## Status

This is a **reference implementation**, provided as-is. It is not a supported product, and it
carries no SLA.

> The GitHub ingestion layer has not yet been verified against a live enterprise tenant. Run
> `scripts/validate_github_api.py` before relying on any output.

## Getting help

**Start with the validation script.** Most issues are permission or policy problems rather than
bugs in this code:

```bash
export GITHUB_TOKEN=...
python scripts/validate_github_api.py --org my-org
```

Then check [docs/operations.md](docs/operations.md#troubleshooting), which maps the common failure
signatures to fixes.

## Common issues

| Symptom | Most likely cause |
|---|---|
| Reports return no download links | The *Copilot usage metrics* policy is not *Enabled everywhere* |
| 403 on billing | Token needs org admin or billing manager |
| `Login failed for user` | `sql/05_grants.sql` was not run |
| Error 51002 | Extract returned nothing — run the validation script |
| Error 51004 | An ingestion run is already in progress |
| Reports show no rows | Row-level security was enabled without granting access in `dbo.report_access` |

## Filing an issue

Include:

- What you expected and what happened
- Output of the validation script (it redacts logins and amounts by default)
- The relevant `dbo.ingestion_run` row
- Azure region and whether you deployed with `azd` or directly

**Do not include** tokens, connection strings, or real user data. For security issues, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.
