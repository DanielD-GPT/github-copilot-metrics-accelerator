"""Load Fabric Warehouse staging tables and invoke the MERGE procedures.

Every load is idempotent: staging is truncated for the window being loaded, rows are
bulk inserted, then a stored procedure upserts into the dims and facts. That makes it
safe to re-run the trailing week to pick up GitHub's billing corrections.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pyodbc

from .config import Settings
from .warehouse_connection import connect

LOGGER = logging.getLogger(__name__)
BATCH_SIZE = 1000

_ACTIVITY_COUNTS = [
    "user_initiated_interaction_count", "code_generation_activity_count",
    "code_acceptance_activity_count", "loc_suggested_to_add_sum",
    "loc_suggested_to_delete_sum", "loc_added_sum", "loc_deleted_sum",
]

USER_DAY_COLUMNS = [
    "activity_date", "user_login", "user_id", "org_login", "organization_id",
    "enterprise_id", "ai_credits_used", "adoption_phase", "adoption_phase_number",
    "used_agent", "used_chat", "used_cli", "used_copilot_app",
    "used_copilot_cloud_agent", "used_code_review_active", "used_code_review_passive",
] + _ACTIVITY_COUNTS

USER_IDE_COLUMNS = [
    "activity_date", "user_login", "org_login", "ide", "ide_family",
    "last_known_ide_version", "last_known_plugin_version",
] + _ACTIVITY_COUNTS

USER_MODEL_FEATURE_COLUMNS = [
    "activity_date", "user_login", "org_login", "model_name", "feature",
] + _ACTIVITY_COUNTS

USER_TEAMS_COLUMNS = [
    "activity_date", "user_login", "user_id", "org_login", "team_id", "team_slug",
]

BILLING_COLUMNS = [
    "usage_date", "user_login", "org_login", "product", "sku", "model_name",
    "unit_type", "price_per_unit", "gross_quantity", "discount_quantity",
    "net_quantity", "gross_amount", "discount_amount", "net_amount",
]

SEAT_COLUMNS = [
    "user_login", "user_id", "org_login", "team_name", "plan_type",
    "created_at", "last_activity_at", "last_activity_editor", "pending_cancellation_date",
]

# Staging table -> column order used for bulk insert.
STAGING_TABLES: dict[str, tuple[str, list[str]]] = {
    "user_day": ("stg.user_day", USER_DAY_COLUMNS),
    "user_ide": ("stg.user_ide", USER_IDE_COLUMNS),
    "user_model_feature": ("stg.user_model_feature", USER_MODEL_FEATURE_COLUMNS),
    "user_teams": ("stg.user_teams", USER_TEAMS_COLUMNS),
    "premium_requests": ("stg.premium_requests", BILLING_COLUMNS),
    "seats": ("stg.seats", SEAT_COLUMNS),
}

# With fast_executemany, pyodbc infers parameter types from the first row. A None or a
# low-precision value there can silently truncate every later row, which on money columns
# produces plausible-looking wrong numbers. Pinning the types removes the guess.


def _text(length: int) -> tuple:
    return (pyodbc.SQL_VARCHAR, length, 0)


_MONEY = (pyodbc.SQL_DECIMAL, 18, 4)
_RATE = (pyodbc.SQL_DECIMAL, 18, 6)
_DATE = _text(10)        # transforms emit ISO date strings
_TIMESTAMP = _text(32)   # ISO 8601 with timezone suffix
_BIGINT = (pyodbc.SQL_BIGINT, 0, 0)
_INT = (pyodbc.SQL_INTEGER, 0, 0)
_BIT = (pyodbc.SQL_TINYINT, 0, 0)

COLUMN_TYPES: dict[str, tuple] = {
    "activity_date": _DATE,
    "usage_date": _DATE,
    "pending_cancellation_date": _DATE,
    "created_at": _TIMESTAMP,
    "last_activity_at": _TIMESTAMP,
    "user_login": _text(100),
    "org_login": _text(100),
    "organization_id": _text(50),
    "enterprise_id": _text(50),
    "user_id": _BIGINT,
    "team_id": _BIGINT,
    "team_slug": _text(200),
    "team_name": _text(200),
    "ai_credits_used": _MONEY,
    "adoption_phase": _text(50),
    "adoption_phase_number": _INT,
    "ide": _text(100),
    "ide_family": _text(50),
    "last_known_ide_version": _text(100),
    "last_known_plugin_version": _text(100),
    "model_name": _text(100),
    "feature": _text(50),
    "product": _text(100),
    "sku": _text(200),
    "unit_type": _text(50),
    "plan_type": _text(50),
    "last_activity_editor": _text(100),
    "price_per_unit": _RATE,
    "gross_quantity": _MONEY,
    "discount_quantity": _MONEY,
    "net_quantity": _MONEY,
    "gross_amount": _MONEY,
    "discount_amount": _MONEY,
    "net_amount": _MONEY,
}

COLUMN_TYPES.update(dict.fromkeys(_ACTIVITY_COUNTS, _INT))
COLUMN_TYPES.update(
    dict.fromkeys(
        [
            "used_agent", "used_chat", "used_cli", "used_copilot_app",
            "used_copilot_cloud_agent", "used_code_review_active", "used_code_review_passive",
        ],
        _BIT,
    )
)


def _calendar_rows(datasets: dict[str, Sequence[dict[str, Any]]]) -> list[tuple[Any, ...]]:
    dates: list[date] = []
    for rows in datasets.values():
        for row in rows:
            for field in ("activity_date", "usage_date"):
                value = row.get(field)
                if value:
                    dates.append(
                        value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
                    )

    if not dates:
        return []

    current = min(dates).replace(day=1)
    latest = max(dates)
    next_month = (
        latest.replace(year=latest.year + 1, month=1, day=1)
        if latest.month == 12
        else latest.replace(month=latest.month + 1, day=1)
    )
    end = next_month - timedelta(days=1)

    rows: list[tuple[Any, ...]] = []
    while current <= end:
        rows.append(
            (
                int(current.strftime("%Y%m%d")),
                current,
                current.year,
                current.month,
                current.day,
                current.strftime("%B"),
                current.strftime("%Y-%m"),
                current.weekday() + 1,
                current.weekday() >= 5,
            )
        )
        current += timedelta(days=1)
    return rows


class WarehouseLoader:
    def __init__(self, settings: Settings):
        self._connection_string = settings.fabric_warehouse_connection_string
        self._fast_executemany = settings.sql_fast_executemany

    def _connect(self, autocommit: bool = False) -> pyodbc.Connection:
        return connect(self._connection_string, autocommit=autocommit)

    def _bulk_insert(
        self,
        cursor: pyodbc.Cursor,
        table: str,
        columns: Sequence[str],
        rows: Sequence[dict[str, Any]],
    ) -> int:
        if not rows:
            return 0

        placeholders = ", ".join(["?"] * len(columns))
        column_list = ", ".join(f"[{c}]" for c in columns)
        # Table and column names come from the STAGING_TABLES constant, never from input.
        # Every value is still bound as a parameter.
        statement = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"  # noqa: S608

        if self._fast_executemany:
            cursor.fast_executemany = True
            cursor.setinputsizes([COLUMN_TYPES.get(c) for c in columns])

        total = 0
        for start in range(0, len(rows), BATCH_SIZE):
            chunk = rows[start : start + BATCH_SIZE]
            cursor.executemany(statement, [[row.get(c) for c in columns] for row in chunk])
            total += len(chunk)

        return total

    @staticmethod
    def _ensure_calendar(cursor: pyodbc.Cursor, datasets: dict[str, Sequence[dict[str, Any]]]) -> None:
        rows = _calendar_rows(datasets)
        if not rows:
            return

        first_key, last_key = rows[0][0], rows[-1][0]
        cursor.execute(
            "SELECT date_key FROM dbo.dim_date WHERE date_key BETWEEN ? AND ?",
            first_key,
            last_key,
        )
        existing = {row[0] for row in cursor.fetchall()}
        missing = [row for row in rows if row[0] not in existing]
        if missing:
            cursor.executemany(
                """
                INSERT INTO dbo.dim_date (
                    date_key, [date], [year], [month], [day],
                    month_name, year_month, day_of_week, is_weekend
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                missing,
            )

    def load(
        self,
        datasets: dict[str, Sequence[dict[str, Any]]],
        strict: bool = True,
        health_check: Callable[[], None] | None = None,
    ) -> dict[str, int]:
        """Stage every dataset and run the merge in one transaction."""
        counts: dict[str, int] = {}

        # pyodbc's context manager commits but does not close, so manage the
        # connection explicitly to avoid leaking one per invocation.
        connection = self._connect()
        try:
            for key, (table, columns) in STAGING_TABLES.items():
                # A cursor per table: setinputsizes persists, so reusing one would
                # carry stale type bindings into the next statement.
                cursor = connection.cursor()
                # DELETE rather than TRUNCATE so the identity needs no ALTER grant.
                # `table` is a module constant, not caller input.
                cursor.execute(f"DELETE FROM {table}")  # noqa: S608
                counts[key] = self._bulk_insert(cursor, table, columns, datasets.get(key) or [])
                cursor.close()
                if health_check:
                    health_check()

            cursor = connection.cursor()
            self._ensure_calendar(cursor, datasets)
            if health_check:
                health_check()
            cursor.execute("{CALL dbo.sp_load_all (?)}", 1 if strict else 0)
            while cursor.nextset():
                pass

            if health_check:
                health_check()
            connection.commit()
        except Exception:
            connection.rollback()
            LOGGER.exception("SQL load failed; transaction rolled back.")
            raise
        finally:
            connection.close()

        LOGGER.info("Staged rows: %s", counts)
        return counts

    def begin_run(self, since, until, trigger_source: str) -> str:
        run_id = str(uuid4())
        connection = self._connect(autocommit=True)
        try:
            connection.cursor().execute(
                "{CALL dbo.sp_begin_run (?, ?, ?, ?, ?)}",
                run_id,
                trigger_source,
                since,
                until,
                120,
            )
            return run_id
        finally:
            connection.close()

    def complete_run(
        self,
        run_id: str,
        status: str,
        counts: dict[str, int] | None = None,
        message: str | None = None,
    ) -> None:
        counts = counts or {}
        connection = self._connect(autocommit=True)
        try:
            connection.cursor().execute(
                "{CALL dbo.sp_complete_run (?, ?, ?, ?, ?, ?, ?, ?)}",
                run_id,
                status,
                counts.get("user_day", 0),
                counts.get("user_ide", 0),
                counts.get("user_model_feature", 0),
                counts.get("premium_requests", 0),
                counts.get("seats", 0),
                (message or "")[:2000],
            )
        except Exception:
            LOGGER.exception("Could not close out ingestion_run %s.", run_id)
        finally:
            connection.close()
