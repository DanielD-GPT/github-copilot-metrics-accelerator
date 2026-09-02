"""Parameter binding tests for the bulk loader.

pyodbc's fast_executemany infers parameter types from the first row. On money columns
that inference can silently truncate later rows, producing wrong-but-plausible dollar
figures. These tests pin the explicit types so the inference never happens.

They exercise binding only. Whether Azure SQL round-trips the values intact still
needs a real database.
"""
from decimal import Decimal

import pytest

pytest.importorskip("pyodbc")

import pyodbc  # noqa: E402

from shared.config import Settings  # noqa: E402
from shared.sql_loader import (  # noqa: E402
    BILLING_COLUMNS,
    COLUMN_TYPES,
    STAGING_TABLES,
    SqlLoader,
)


class _FakeCursor:
    def __init__(self):
        self.fast_executemany = False
        self.input_sizes = None
        self.batches = []
        self.statements = []

    def setinputsizes(self, sizes):
        self.input_sizes = sizes

    def executemany(self, statement, params):
        self.statements.append(statement)
        self.batches.append(params)


def _loader(**overrides):
    base = {
        "github_orgs": ["acme"],
        "key_vault_name": "kv",
        "sql_connection_string": "Driver=...",
        "lake_account_name": "lake",
    }
    base.update(overrides)
    return SqlLoader(Settings(**base))


class TestColumnTypeMap:
    def test_every_staged_column_has_a_pinned_type(self):
        # An unmapped column falls back to inference, which is the bug being prevented.
        missing = {
            column
            for _table, columns in STAGING_TABLES.values()
            for column in columns
            if column not in COLUMN_TYPES
        }
        assert missing == set()

    def test_money_columns_use_decimal_18_4(self):
        for column in (
            "net_amount", "gross_amount", "discount_amount",
            "net_quantity", "gross_quantity", "discount_quantity",
            "ai_credits_used",
        ):
            assert COLUMN_TYPES[column] == (pyodbc.SQL_DECIMAL, 18, 4), column

    def test_unit_price_keeps_six_decimal_places(self):
        # Per-request prices are fractions of a cent; 4 places would round them away.
        assert COLUMN_TYPES["price_per_unit"] == (pyodbc.SQL_DECIMAL, 18, 6)

    def test_identifiers_are_bigint(self):
        assert COLUMN_TYPES["user_id"] == (pyodbc.SQL_BIGINT, 0, 0)
        assert COLUMN_TYPES["team_id"] == (pyodbc.SQL_BIGINT, 0, 0)


class TestBulkInsertBinding:
    def _rows(self):
        return [
            # First row carries nulls: the case that poisons type inference.
            {"usage_date": "2026-08-01", "user_login": "a", "net_amount": None,
             "price_per_unit": None},
            {"usage_date": "2026-08-02", "user_login": "b",
             "net_amount": Decimal("12345.6789"), "price_per_unit": Decimal("0.000125")},
        ]

    def test_declares_types_in_column_order(self):
        cursor = _FakeCursor()
        _loader()._bulk_insert(cursor, "stg.premium_requests", BILLING_COLUMNS, self._rows())

        assert cursor.fast_executemany is True
        assert cursor.input_sizes == [COLUMN_TYPES[c] for c in BILLING_COLUMNS]

    def test_decimal_values_are_passed_through_unconverted(self):
        cursor = _FakeCursor()
        _loader()._bulk_insert(cursor, "stg.premium_requests", BILLING_COLUMNS, self._rows())

        net_index = BILLING_COLUMNS.index("net_amount")
        values = [row[net_index] for row in cursor.batches[0]]
        # Still Decimal, not float: float conversion is where precision would be lost.
        assert values[1] == Decimal("12345.6789")
        assert isinstance(values[1], Decimal)

    def test_missing_keys_bind_as_null(self):
        cursor = _FakeCursor()
        _loader()._bulk_insert(cursor, "stg.premium_requests", BILLING_COLUMNS, [{"user_login": "a"}])
        assert cursor.batches[0][0][BILLING_COLUMNS.index("net_amount")] is None

    def test_escape_hatch_skips_type_pinning(self):
        cursor = _FakeCursor()
        loader = _loader(sql_fast_executemany=False)
        loader._bulk_insert(cursor, "stg.premium_requests", BILLING_COLUMNS, self._rows())

        assert cursor.fast_executemany is False
        assert cursor.input_sizes is None

    def test_empty_rows_do_nothing(self):
        cursor = _FakeCursor()
        assert _loader()._bulk_insert(cursor, "stg.premium_requests", BILLING_COLUMNS, []) == 0
        assert cursor.batches == []

    def test_batches_respect_the_chunk_size(self):
        from shared.sql_loader import BATCH_SIZE

        rows = [{"user_login": f"u{i}"} for i in range(BATCH_SIZE + 5)]
        cursor = _FakeCursor()
        total = _loader()._bulk_insert(cursor, "stg.premium_requests", BILLING_COLUMNS, rows)

        assert total == BATCH_SIZE + 5
        assert len(cursor.batches) == 2
