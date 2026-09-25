"""Regression tests for the raw-zone archive.

An earlier revision archived flattened billing rows, which carry Decimal values.
json.dumps cannot serialize Decimal, so every ingestion run that reached the billing
step raised TypeError and loaded nothing. Both the encoder and the archive-raw
behaviour are pinned here.
"""
import json
from datetime import date, datetime
from decimal import Decimal

import pytest

pytest.importorskip("azure.storage.filedatalake")
pytest.importorskip("azure.functions")

from shared.config import Settings  # noqa: E402
from shared.lake import _json_default  # noqa: E402
from shared.transform import flatten_premium_requests  # noqa: E402


class TestJsonDefault:
    def test_decimal_serializes_as_string(self):
        # str, not float: float("0.1") + float("0.2") style drift is unacceptable on money.
        assert _json_default(Decimal("123.4567")) == "123.4567"

    def test_decimal_keeps_full_precision(self):
        value = Decimal("0.123456789012345678")
        assert _json_default(value) == "0.123456789012345678"
        assert float(_json_default(value)) != float(0)  # sanity

    def test_dates_serialize_isoformat(self):
        assert _json_default(date(2026, 8, 1)) == "2026-08-01"
        assert _json_default(datetime(2026, 8, 1, 12, 30)) == "2026-08-01T12:30:00"

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            _json_default(object())

    def test_flattened_billing_rows_survive_dumps(self, billing_payload):
        rows = flatten_premium_requests(billing_payload, "acme", "jimmy", date(2026, 8, 1))
        encoded = json.dumps(rows, default=_json_default)
        assert "50.0" in encoded


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def premium_request_usage(self, org, user, year, month, day=None):
        self.calls.append((org, user, year, month, day))
        return self._payload


class _FakeLake:
    def __init__(self):
        self.writes = []

    def write(self, dataset, partition_date, payload, suffix=""):
        # The archive must be plain JSON with no custom encoder. If this raises,
        # we are archiving transformed data rather than the raw response.
        json.dumps(payload)
        self.writes.append((dataset, suffix, payload))
        return f"{dataset}/{suffix}"


def _settings(**overrides):
    base = {
        "github_orgs": ["acme"],
        "key_vault_name": "kv",
        "fabric_warehouse_connection_string": "Driver=...",
        "lake_account_name": "lake",
        "max_billing_users": 100,
    }
    base.update(overrides)
    return Settings(**base)


class TestPullBillingArchive:
    def test_archives_raw_payload_not_flattened_rows(self, billing_payload):
        from function_app import _pull_billing

        client = _FakeClient(billing_payload)
        lake = _FakeLake()
        seats = [{"user_login": "jimmy"}]

        rows = _pull_billing(
            client, lake, _settings(), "acme", seats, date(2026, 8, 1), date(2026, 8, 1)
        )

        assert len(lake.writes) == 1
        dataset, suffix, archived = lake.writes[0]
        assert dataset == "premium_requests"
        assert suffix == "acme"

        entry = archived[0]
        assert entry["user_login"] == "jimmy"
        assert entry["payload"] == billing_payload
        # The flattened rows are returned for loading, not archived.
        assert rows[0]["net_amount"] == Decimal("50.0")

    def test_nothing_archived_when_no_usage(self):
        from function_app import _pull_billing

        client = _FakeClient({})
        lake = _FakeLake()
        rows = _pull_billing(
            client, lake, _settings(), "acme", [{"user_login": "jimmy"}],
            date(2026, 8, 1), date(2026, 8, 1),
        )

        assert rows == []
        assert lake.writes == []

    def test_respects_max_billing_users(self, billing_payload):
        from function_app import _pull_billing

        client = _FakeClient(billing_payload)
        seats = [{"user_login": f"user{i}"} for i in range(10)]

        _pull_billing(
            client, _FakeLake(), _settings(max_billing_users=3), "acme", seats,
            date(2026, 8, 1), date(2026, 8, 1),
        )

        assert len({call[1] for call in client.calls}) == 3

    def test_one_call_per_user_per_day(self, billing_payload):
        from function_app import _pull_billing

        client = _FakeClient(billing_payload)
        seats = [{"user_login": "a"}, {"user_login": "b"}]

        _pull_billing(
            client, _FakeLake(), _settings(), "acme", seats,
            date(2026, 8, 1), date(2026, 8, 3),
        )

        # 2 users x 3 days
        assert len(client.calls) == 6


class TestPathSanitization:
    """Org and dataset names become blob path segments."""

    def test_strips_traversal_characters(self):
        from shared.lake import _safe_segment

        for hostile in ("../../etc/passwd", "org/with/slashes", "org\\with\\backslashes", ".."):
            result = _safe_segment(hostile)
            assert "/" not in result
            assert "\\" not in result
            assert not result.startswith(".")

    def test_preserves_ordinary_names(self):
        from shared.lake import _safe_segment

        assert _safe_segment("acme-corp") == "acme-corp"
        assert _safe_segment("users_1_day") == "users_1_day"

    def test_never_returns_empty(self):
        from shared.lake import _safe_segment

        for value in ("", "...", "///"):
            assert _safe_segment(value)


class TestSafeErrorText:
    def test_omits_driver_detail(self):
        from function_app import _safe_error

        exc = RuntimeError(
            "[08001] Server=tcp:sql-abc.database.windows.net;Pwd=hunter2;Login failed"
        )
        message = _safe_error(exc)

        assert "RuntimeError" in message
        assert "hunter2" not in message
        assert "database.windows.net" not in message


class TestSeatsGuard:
    """No seats means no billing fan-out, which would look like zero spend."""

    def test_raises_when_no_seats(self, billing_payload):
        from function_app import SeatsUnavailable, _pull_billing

        with pytest.raises(SeatsUnavailable, match="no Copilot seats"):
            _pull_billing(
                _FakeClient(billing_payload), _FakeLake(), _settings(), "acme", [],
                date(2026, 8, 1), date(2026, 8, 1),
            )

    def test_raises_when_seats_have_no_logins(self, billing_payload):
        from function_app import SeatsUnavailable, _pull_billing

        # A seat with no assignee yields a blank login and must not count.
        with pytest.raises(SeatsUnavailable):
            _pull_billing(
                _FakeClient(billing_payload), _FakeLake(), _settings(),
                "acme", [{"user_login": ""}], date(2026, 8, 1), date(2026, 8, 1),
            )

    def test_downgrades_to_warning_when_not_strict(self, billing_payload, caplog):
        from function_app import _pull_billing

        client = _FakeClient(billing_payload)
        rows = _pull_billing(
            client, _FakeLake(), _settings(fail_on_empty_report=False), "acme", [],
            date(2026, 8, 1), date(2026, 8, 1),
        )

        assert rows == []
        assert client.calls == []
        assert "no Copilot seats" in caplog.text
