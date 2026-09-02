"""Azure Functions entry point.

  * ingest_daily   — timer, 02:00 UTC, reloads a trailing window
  * ingest_backfill — HTTP, seeds history or re-runs an explicit date range
  * api.py          — internal read-only REST API
  * teams.py        — Teams bot webhook
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta

import azure.functions as func
from api import bp as api_bp
from teams import bp as teams_bp

from shared.config import load_settings
from shared.github_client import GitHubClient
from shared.lake import RawZone
from shared.sql_loader import RunInProgress, SqlLoader
from shared.transform import (
    flatten_premium_requests,
    flatten_seats,
    flatten_user_day,
    flatten_user_ide,
    flatten_user_model_feature,
    flatten_user_teams,
)

app = func.FunctionApp()
app.register_blueprint(api_bp)
app.register_blueprint(teams_bp)

LOGGER = logging.getLogger(__name__)


class SeatsUnavailable(RuntimeError):
    """An org returned no Copilot seats, so its spend cannot be attributed."""

DATASET_KEYS = (
    "user_day",
    "user_ide",
    "user_model_feature",
    "user_teams",
    "premium_requests",
    "seats",
)


def _daterange(since: date, until: date):
    current = since
    while current <= until:
        yield current
        current += timedelta(days=1)


def run_ingestion(since: date, until: date, trigger_source: str = "timer") -> dict[str, int]:
    settings = load_settings()
    settings.validate()

    client = GitHubClient(settings)
    lake = RawZone(settings)
    loader = SqlLoader(settings)

    # Claim the lock before any extraction so a concurrent run fails fast and cheap.
    run_id = loader.begin_run(since, until, trigger_source)

    datasets: dict[str, list[dict]] = {key: [] for key in DATASET_KEYS}

    try:
        # Metrics and billing are both pulled per organization: the billing endpoint is
        # org-scoped, and org scope is what gives each metrics record a usable org login.
        for org in settings.github_orgs:
            seats = client.copilot_seats(org)
            lake.write("seats", until, seats, suffix=org)
            seat_rows = flatten_seats(seats, org)
            datasets["seats"].extend(seat_rows)

            for day in _daterange(since, until):
                users = client.user_metrics(day, org)
                if users:
                    lake.write("users_1_day", day, users, suffix=org)
                    datasets["user_day"].extend(flatten_user_day(users, org))
                    datasets["user_ide"].extend(flatten_user_ide(users, org))
                    datasets["user_model_feature"].extend(flatten_user_model_feature(users, org))

                teams = client.user_teams(day, org)
                if teams:
                    lake.write("user_teams_1_day", day, teams, suffix=org)
                    datasets["user_teams"].extend(flatten_user_teams(teams, org))

            datasets["premium_requests"].extend(
                _pull_billing(client, lake, settings, org, seat_rows, since, until)
            )

        counts = loader.load(datasets, strict=settings.fail_on_empty_report)
    except Exception as exc:
        loader.complete_run(run_id, "failed", {}, str(exc))
        raise

    loader.complete_run(run_id, "succeeded", counts)
    LOGGER.info("Ingestion complete for %s..%s: %s", since, until, counts)
    return counts


def _pull_billing(client, lake, settings, org: str, seat_rows, since: date, until: date) -> list[dict]:
    """Per-user spend. The billing API only attributes cost when filtered by user,
    so this costs one request per user per period."""
    logins = sorted({row["user_login"] for row in seat_rows if row.get("user_login")})

    if not logins:
        # Without seats there is nobody to bill against, so the run would report
        # success with zero spend. That is the failure mode this guards.
        message = (
            f"Org {org} returned no Copilot seats, so no spend can be attributed. "
            "This usually means the token lacks org read or billing manager permission."
        )
        if settings.fail_on_empty_report:
            raise SeatsUnavailable(message)
        LOGGER.warning(message)
        return []

    if len(logins) > settings.max_billing_users:
        LOGGER.warning(
            "Org %s has %s seats, above MAX_BILLING_USERS=%s; truncating billing pull.",
            org, len(logins), settings.max_billing_users,
        )
        logins = logins[: settings.max_billing_users]

    days = list(_daterange(since, until))
    LOGGER.info("Billing pull for %s: %s users x %s days.", org, len(logins), len(days))

    rows: list[dict] = []
    raw: list[dict] = []

    for login in logins:
        for day in days:
            payload = client.premium_request_usage(org, login, day.year, day.month, day.day)
            if not payload:
                continue

            # Archive the untouched response, not the flattened rows: the archive is
            # only a replay source if it predates the transform.
            raw.append(
                {
                    "user_login": login,
                    "period": day.isoformat(),
                    "payload": payload,
                }
            )
            rows.extend(flatten_premium_requests(payload, org, login, day))

    if raw:
        lake.write("premium_requests", until, raw, suffix=org)
    return rows


@app.function_name(name="ingest_daily")
@app.timer_trigger(schedule="%INGESTION_SCHEDULE%", arg_name="timer", run_on_startup=False)
def ingest_daily(timer: func.TimerRequest) -> None:
    settings = load_settings()
    until = datetime.now(UTC).date() - timedelta(days=1)
    since = until - timedelta(days=settings.reload_trailing_days)

    if timer.past_due:
        LOGGER.warning("Timer is past due; running immediately.")

    try:
        run_ingestion(since, until, trigger_source="timer")
    except RunInProgress:
        LOGGER.warning("Skipping scheduled run: another ingestion is already in progress.")


@app.function_name(name="ingest_backfill")
@app.route(route="backfill", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def ingest_backfill(req: func.HttpRequest) -> func.HttpResponse:
    settings = load_settings()

    try:
        body = req.get_json()
    except ValueError:
        body = {}

    until = _parse_date(body.get("until")) or datetime.now(UTC).date() - timedelta(days=1)
    since = _parse_date(body.get("since")) or until - timedelta(days=settings.backfill_days)

    if since > until:
        return func.HttpResponse(
            json.dumps({"error": "'since' must be on or before 'until'."}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        counts = run_ingestion(since, until, trigger_source="backfill")
    except RunInProgress:
        return func.HttpResponse(
            json.dumps({"status": "conflict", "error": "An ingestion run is already in progress."}),
            status_code=409,
            mimetype="application/json",
        )
    except Exception:
        # Exception text can carry server names and connection detail; keep it in logs only.
        LOGGER.exception("Backfill failed.")
        return func.HttpResponse(
            json.dumps({"status": "failed", "error": "Ingestion failed. See Application Insights."}),
            status_code=500,
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps({"status": "ok", "since": since.isoformat(), "until": until.isoformat(), "rows": counts}),
        status_code=200,
        mimetype="application/json",
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])
