"""Read-only queries behind the internal API and Teams bot.

All user-supplied values are bound as parameters. Never interpolate request input
into these statements — the API is internal, but it is still a SQL injection surface.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .config import Settings
from .warehouse_connection import connect

LOGGER = logging.getLogger(__name__)

MAX_LIMIT = 500
DEFAULT_LIMIT = 25
DEFAULT_WINDOW_DAYS = 30


def jsonable(value: Any) -> Any:
    """Decimals and dates are not JSON serializable by default."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class SpendQueries:
    def __init__(self, settings: Settings):
        self._connection_string = settings.fabric_warehouse_connection_string

    def _fetch(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        # pyodbc's context manager commits but does not close, so close explicitly.
        connection = connect(self._connection_string)
        try:
            cursor = connection.cursor()
            cursor.execute(sql, *params)
            columns = [c[0] for c in cursor.description]
            return [
                {col: jsonable(val) for col, val in zip(columns, row, strict=True)}
                for row in cursor.fetchall()
            ]
        finally:
            connection.close()

    @staticmethod
    def resolve_window(since: date | None, until: date | None) -> tuple[date, date]:
        resolved_until = until or date.today()
        resolved_since = since or resolved_until - timedelta(days=DEFAULT_WINDOW_DAYS)
        if resolved_since > resolved_until:
            raise ValueError("'since' must be on or before 'until'.")
        return resolved_since, resolved_until

    @staticmethod
    def clamp_limit(limit: int | None) -> int:
        if not limit or limit < 1:
            return DEFAULT_LIMIT
        return min(limit, MAX_LIMIT)

    # ------------------------------------------------------------------ queries

    def user_breakdown(self, login: str, since: date, until: date) -> list[dict[str, Any]]:
        """The headline question: one user's spend split by model, editor, and feature."""
        sql = """
            SELECT
                model_name,
                editor_family,
                feature,
                attribution_quality,
                SUM(allocated_net_amount) AS spend,
                SUM(allocated_quantity)   AS requests
            FROM dbo.vw_spend_by_user_model_editor
            WHERE user_login = ?
              AND usage_date BETWEEN ? AND ?
            GROUP BY model_name, editor_family, feature, attribution_quality
            HAVING SUM(allocated_net_amount) > 0
            ORDER BY spend DESC;
        """
        return self._fetch(sql, (login, since, until))

    def user_exact_total(self, login: str, since: date, until: date) -> dict[str, Any]:
        """Exact, unmodelled total for the same user and window."""
        sql = """
            SELECT
                COALESCE(SUM(net_amount), 0) AS net_amount,
                COALESCE(SUM(quantity), 0)   AS quantity,
                COUNT(DISTINCT model_name)   AS models_used
            FROM dbo.vw_spend_by_user_model
            WHERE user_login = ?
              AND usage_date BETWEEN ? AND ?;
        """
        rows = self._fetch(sql, (login, since, until))
        return rows[0] if rows else {"net_amount": 0, "quantity": 0, "models_used": 0}

    def top_spenders(self, since: date, until: date, limit: int) -> list[dict[str, Any]]:
        sql = """
            SELECT TOP (?)
                user_login,
                team_name,
                cost_center,
                SUM(net_amount) AS spend
            FROM dbo.vw_spend_by_user_model
            WHERE usage_date BETWEEN ? AND ?
            GROUP BY user_login, team_name, cost_center
            ORDER BY spend DESC;
        """
        return self._fetch(sql, (limit, since, until))

    def team_breakdown(self, team: str, since: date, until: date) -> list[dict[str, Any]]:
        sql = """
            SELECT
                user_login,
                model_name,
                SUM(net_amount) AS spend
            FROM dbo.vw_spend_by_user_model
            WHERE COALESCE(cost_center, team_name, 'unassigned') = ?
              AND usage_date BETWEEN ? AND ?
            GROUP BY user_login, model_name
            ORDER BY spend DESC;
        """
        return self._fetch(sql, (team, since, until))

    def summary(self, since: date, until: date) -> dict[str, Any]:
        sql = """
            SELECT
                COALESCE(SUM(net_amount), 0)   AS total_spend,
                COUNT(DISTINCT user_login)     AS active_users,
                COUNT(DISTINCT model_name)     AS models_used,
                COALESCE(SUM(quantity), 0)     AS total_requests
            FROM dbo.vw_spend_by_user_model
            WHERE usage_date BETWEEN ? AND ?;
        """
        rows = self._fetch(sql, (since, until))
        return rows[0] if rows else {}

    def editor_mix(self, since: date, until: date) -> list[dict[str, Any]]:
        sql = """
            SELECT
                editor_family,
                feature,
                SUM(allocated_net_amount) AS spend
            FROM dbo.vw_spend_by_user_model_editor
            WHERE usage_date BETWEEN ? AND ?
            GROUP BY editor_family, feature
            HAVING SUM(allocated_net_amount) > 0
            ORDER BY spend DESC;
        """
        return self._fetch(sql, (since, until))

    def data_freshness(self) -> dict[str, Any]:
        sql = """
            SELECT
                (SELECT MAX(d.[date]) FROM dbo.fact_premium_requests f
                    JOIN dbo.dim_date d ON d.date_key = f.date_key) AS latest_billing_date,
                (SELECT MAX(d.[date]) FROM dbo.fact_user_day f
                    JOIN dbo.dim_date d ON d.date_key = f.date_key) AS latest_activity_date,
                (SELECT MAX(completed_at) FROM dbo.ingestion_run WHERE status = 'succeeded')
                    AS last_successful_run;
        """
        rows = self._fetch(sql, ())
        return rows[0] if rows else {}
