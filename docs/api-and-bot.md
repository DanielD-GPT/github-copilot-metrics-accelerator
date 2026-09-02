# Internal API & Teams bot

Both run inside the same Function App as the ingestion, so they add no infrastructure and no
additional cost.

---

## REST API

All endpoints require a function key, except `/api/health`.

```bash
FUNC=$(azd env get-value FUNCTION_APP_NAME)
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
KEY=$(az functionapp keys list -g "$RG" -n "$FUNC" --query functionKeys.default -o tsv)
BASE="https://$FUNC.azurewebsites.net/api"
```

| Endpoint | Parameters | Returns |
|---|---|---|
| `GET /spend/user` | `login` (required), `since`, `until` | Exact total + allocated breakdown |
| `GET /spend/team` | `team` (required), `since`, `until` | Per-user spend for a cost center |
| `GET /spend/top` | `limit` (default 25, max 500), `since`, `until` | Highest spenders |
| `GET /spend/summary` | `since`, `until` | Enterprise totals |
| `GET /spend/editors` | `since`, `until` | Allocated spend by editor and feature |
| `GET /health` | — | Data freshness; no key required |

Dates are ISO `YYYY-MM-DD`. Omitting them defaults to the trailing 30 days.

### Example

```bash
curl -s "$BASE/spend/user?login=jimmy&since=2026-08-01&until=2026-08-31&code=$KEY" | jq
```

```json
{
  "user_login": "jimmy",
  "since": "2026-08-01",
  "until": "2026-08-31",
  "exact_total": { "net_amount": 100.0, "quantity": 2000.0, "models_used": 3 },
  "breakdown": [
    { "model_name": "gpt-5.3", "editor_family": "VS Code", "feature": "chat",
      "attribution_quality": "modelled", "spend": 50.0, "requests": 1000.0 },
    { "model_name": "gpt-5.6", "editor_family": "VS Code", "feature": "chat",
      "attribution_quality": "modelled", "spend": 30.0, "requests": 600.0 },
    { "model_name": "gpt-5.4", "editor_family": "Visual Studio", "feature": "chat",
      "attribution_quality": "modelled", "spend": 20.0, "requests": 400.0 }
  ],
  "attribution_note": "Per-user and per-model dollar amounts are exact. The editor/feature split is a proportional allocation derived from aggregate engagement data, not an audit record."
}
```

`exact_total.net_amount` is authoritative. The `breakdown` array sums to it, but its editor split
is modelled — always render `attribution_note` alongside it.

### Security

- All SQL is parameterized; request values are never concatenated into statements.
- Endpoints are read-only — no route writes to the database.
- `limit` is clamped server-side to 500.
- For production, put the Function behind API Management or add IP restrictions, and rotate the
  function key on a schedule.

---

## Teams bot

Implemented as a **Teams Outgoing Webhook**, which needs no Azure Bot Service resource and no
separate app registration. Teams signs every request with a shared secret that the function
verifies using HMAC-SHA256 with a constant-time comparison.

### Setup

1. In Teams, open the target team → **⋯ → Manage team → Apps → Create an outgoing webhook**.

2. Fill in:
   - **Name:** `Copilot Spend`
   - **Callback URL:** `https://<your-function-app>.azurewebsites.net/api/teams`
   - **Description:** `Query GitHub Copilot spend`

3. Teams shows a **security token exactly once**. Copy it, then store it in Key Vault:

   ```bash
   az keyvault secret set \
     --vault-name "$(azd env get-value AZURE_KEY_VAULT_NAME)" \
     --name teams-webhook-secret \
     --value "<token-from-teams>"
   ```

   Store the token verbatim — it is already base64 and the function decodes it before computing
   the HMAC.

4. Test in the channel:

   ```
   @Copilot Spend summary
   ```

### Commands

| Command | Result |
|---|---|
| `@bot jimmy` | Spend breakdown for that user |
| `@bot me` | Spend for the caller |
| `@bot top 10` | Highest spenders |
| `@bot team Platform` | Spend for a team or cost center |
| `@bot summary` | Enterprise totals |
| `@bot help` | Command list |

All responses use Adaptive Cards. User-detail cards carry the attribution caveat automatically.

### Notes and limitations

- `@bot me` matches the caller's **Teams display name** against `dim_user.user_login`. If those
  differ in your tenant — which is common — add a mapping table and resolve it in `_handle` in
  [../src/functions/teams.py](../src/functions/teams.py).
- Outgoing webhooks are scoped to a single team. Add the webhook separately per team, reusing the
  same callback URL; each team issues its own token, so extend `_verify_signature` to try a small
  set of secrets if you need more than one.
- The route is `AuthLevel.ANONYMOUS` at the platform level **by design** — Teams cannot send a
  function key. Authentication is the HMAC signature. Requests with a missing or invalid signature
  are rejected with 401 before any query runs.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Unauthorized` | Secret mismatch | Re-copy the Teams token; confirm the Key Vault secret name matches `TEAMS_WEBHOOK_SECRET_NAME` |
| `Bot not configured.` | Function cannot read Key Vault | Confirm the managed identity has **Key Vault Secrets User** |
| Bot never responds | Callback URL wrong | Must be the full `https://.../api/teams` |
| "No spend recorded" for a real user | Login mismatch | Compare against `SELECT user_login FROM dbo.dim_user` |
