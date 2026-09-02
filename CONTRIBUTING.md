# Contributing

## Before you start

Run the checks locally. CI runs the same three.

```bash
pip install -r src/functions/requirements.txt
pip install ruff pytest

ruff check .
python -m compileall -q src/functions scripts
python -m pytest tests -q

az bicep build --file infra/main.bicep --stdout > /dev/null
```

## Ground rules

**Never commit secrets.** No tokens, connection strings, private keys, or real GitHub logins. Use
`src/functions/local.settings.json.sample` as the template; the real file is gitignored.

**Archive before parsing.** Any new data source must write its raw payload to the lake with
`lake.write(...)` *before* transforming it. That archive is what makes loads replayable.

**Fail loudly.** Silent failure is the worst outcome for this project, because wrong numbers get
attributed to named people. New sources should extend `dbo.sp_reconcile_load` so dropped rows
raise rather than pass.

**Be honest about accuracy.** If a number is modelled rather than measured, say so in the view, in
the API response, and on the report. Do not describe an estimate as exact.

## Adding a data source

1. Fetch method on `GitHubClient` in [src/functions/shared/github_client.py](src/functions/shared/github_client.py)
2. Flattener in [shared/transform.py](src/functions/shared/transform.py), with tests
3. Staging table in [sql/02_staging.sql](sql/02_staging.sql), registered in `STAGING_TABLES`
4. MERGE procedure called from `dbo.sp_load_all`
5. Reconciliation check in `dbo.sp_reconcile_load`

## Testing

Tests live in `tests/` and cover the pure functions — flatteners, config validation, and the Teams
signature check. They need no Azure resources and no network.

Anything touching GitHub or SQL is not unit tested. To verify against a real tenant use the
read-only probe:

```bash
export GITHUB_TOKEN=...
python scripts/validate_github_api.py --org my-org
```

## Style

- `ruff` settings are in [ruff.toml](ruff.toml); line length 110
- Comments explain *why*, not *what*
- SQL: lowercase keywords for column lists, uppercase for statements, matching existing files
- Bicep: one resource per logical component, modules under `infra/core/`

## Pull requests

Keep them focused. Describe what changed and why, note any accuracy or privacy implications, and
confirm the checks above pass.
