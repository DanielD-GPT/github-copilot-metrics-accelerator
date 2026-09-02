"""Internal read-only REST API over the warehouse.

Routes (all require a function key):
    GET /api/spend/user?login=jimmy&since=&until=
    GET /api/spend/team?team=Platform
    GET /api/spend/top?limit=20
    GET /api/spend/summary
    GET /api/spend/editors
    GET /api/health
"""
from __future__ import annotations

import json
import logging
from datetime import date

import azure.functions as func

from shared.config import load_settings
from shared.queries import SpendQueries

bp = func.Blueprint()
LOGGER = logging.getLogger(__name__)

# Applied to modelled figures so consumers cannot silently present them as exact.
ATTRIBUTION_NOTE = (
    "Per-user and per-model dollar amounts are exact. The editor/feature split is a "
    "proportional allocation derived from aggregate engagement data, not an audit record."
)


def _json(payload: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, default=str),
        status_code=status,
        mimetype="application/json",
    )


def _error(message: str, status: int = 400) -> func.HttpResponse:
    return _json({"error": message}, status)


def _param_date(req: func.HttpRequest, name: str) -> date | None:
    raw = req.params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ValueError(f"'{name}' must be an ISO date (YYYY-MM-DD).") from exc


def _window(req: func.HttpRequest) -> tuple[date, date]:
    return SpendQueries.resolve_window(_param_date(req, "since"), _param_date(req, "until"))


@bp.function_name(name="spend_user")
@bp.route(route="spend/user", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def spend_user(req: func.HttpRequest) -> func.HttpResponse:
    login = (req.params.get("login") or "").strip()
    if not login:
        return _error("Query parameter 'login' is required.")

    try:
        since, until = _window(req)
    except ValueError as exc:
        return _error(str(exc))

    queries = SpendQueries(load_settings())
    try:
        breakdown = queries.user_breakdown(login, since, until)
        exact = queries.user_exact_total(login, since, until)
    except Exception:
        LOGGER.exception("spend_user query failed.")
        return _error("Query failed.", 500)

    return _json(
        {
            "user_login": login,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "exact_total": exact,
            "breakdown": breakdown,
            "attribution_note": ATTRIBUTION_NOTE,
        }
    )


@bp.function_name(name="spend_team")
@bp.route(route="spend/team", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def spend_team(req: func.HttpRequest) -> func.HttpResponse:
    team = (req.params.get("team") or "").strip()
    if not team:
        return _error("Query parameter 'team' is required.")

    try:
        since, until = _window(req)
    except ValueError as exc:
        return _error(str(exc))

    queries = SpendQueries(load_settings())
    try:
        rows = queries.team_breakdown(team, since, until)
    except Exception:
        LOGGER.exception("spend_team query failed.")
        return _error("Query failed.", 500)

    return _json(
        {
            "team": team,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "total_spend": sum(r.get("spend") or 0 for r in rows),
            "rows": rows,
        }
    )


@bp.function_name(name="spend_top")
@bp.route(route="spend/top", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def spend_top(req: func.HttpRequest) -> func.HttpResponse:
    try:
        since, until = _window(req)
        raw_limit = req.params.get("limit")
        limit = SpendQueries.clamp_limit(int(raw_limit) if raw_limit else None)
    except ValueError as exc:
        return _error(str(exc))

    queries = SpendQueries(load_settings())
    try:
        rows = queries.top_spenders(since, until, limit)
    except Exception:
        LOGGER.exception("spend_top query failed.")
        return _error("Query failed.", 500)

    return _json({"since": since.isoformat(), "until": until.isoformat(), "rows": rows})


@bp.function_name(name="spend_summary")
@bp.route(route="spend/summary", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def spend_summary(req: func.HttpRequest) -> func.HttpResponse:
    try:
        since, until = _window(req)
    except ValueError as exc:
        return _error(str(exc))

    queries = SpendQueries(load_settings())
    try:
        payload = queries.summary(since, until)
    except Exception:
        LOGGER.exception("spend_summary query failed.")
        return _error("Query failed.", 500)

    payload.update({"since": since.isoformat(), "until": until.isoformat()})
    return _json(payload)


@bp.function_name(name="spend_editors")
@bp.route(route="spend/editors", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def spend_editors(req: func.HttpRequest) -> func.HttpResponse:
    try:
        since, until = _window(req)
    except ValueError as exc:
        return _error(str(exc))

    queries = SpendQueries(load_settings())
    try:
        rows = queries.editor_mix(since, until)
    except Exception:
        LOGGER.exception("spend_editors query failed.")
        return _error("Query failed.", 500)

    return _json(
        {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "rows": rows,
            "attribution_note": ATTRIBUTION_NOTE,
        }
    )


@bp.function_name(name="health")
@bp.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    try:
        freshness = SpendQueries(load_settings()).data_freshness()
    except Exception:
        LOGGER.exception("Health check could not reach SQL.")
        return _json({"status": "degraded", "database": "unreachable"}, 503)

    return _json({"status": "ok", "database": "reachable", **freshness})
