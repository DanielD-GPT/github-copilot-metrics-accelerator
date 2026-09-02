"""Guards on untrusted input: report download links and backfill requests.

Report links come from an API response, so the downloader treats them as untrusted.
Backfill parameters come from an HTTP caller.
"""
import json
from datetime import date, timedelta

import pytest

pytest.importorskip("requests")
pytest.importorskip("azure.keyvault.secrets")

from shared.config import Settings  # noqa: E402
from shared.github_client import GitHubClient, GitHubError  # noqa: E402


def _client(**overrides):
    base = {
        "github_orgs": ["acme"],
        "key_vault_name": "kv",
        "sql_connection_string": "Driver=...",
        "lake_account_name": "lake",
    }
    base.update(overrides)
    # credential is unused by the URL guard; avoid touching Azure identity.
    return GitHubClient(Settings(**base), credential=object())


class TestDownloadUrlGuard:
    def test_accepts_allowlisted_hosts(self):
        client = _client()
        for url in (
            "https://api.github.com/reports/x.ndjson",
            "https://objects.githubusercontent.com/report.gz",
            "https://myaccount.blob.core.windows.net/c/report.ndjson",
        ):
            client._check_download_url(url)

    def test_rejects_non_https(self):
        with pytest.raises(GitHubError, match="https required"):
            _client()._check_download_url("http://api.github.com/report")

    def test_rejects_unexpected_host(self):
        with pytest.raises(GitHubError, match="unexpected host"):
            _client()._check_download_url("https://evil.example.com/report")

    def test_rejects_link_local_metadata_endpoint(self):
        # The classic SSRF target from inside a cloud VM.
        with pytest.raises(GitHubError):
            _client()._check_download_url("https://169.254.169.254/metadata/identity")

    def test_rejects_suffix_confusion(self):
        # 'notgithub.com' must not satisfy an allowlist entry of 'github.com'.
        with pytest.raises(GitHubError, match="unexpected host"):
            _client()._check_download_url("https://notgithub.com/report")

    def test_rejects_host_embedded_in_path(self):
        with pytest.raises(GitHubError, match="unexpected host"):
            _client()._check_download_url("https://evil.example.com/https://github.com/report")

    def test_honours_a_custom_allowlist(self):
        client = _client(report_host_allowlist=("internal.contoso.com",))
        client._check_download_url("https://reports.internal.contoso.com/x")
        with pytest.raises(GitHubError):
            client._check_download_url("https://api.github.com/report")


class TestDecompressionCap:
    def test_rejects_a_zip_bomb(self):
        import zlib

        # ~10 MB of zeroes compresses to a few KB.
        payload = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        blob = payload.compress(b"\0" * (10 * 1024 * 1024)) + payload.flush()

        client = _client(max_report_bytes=1024)
        with pytest.raises(GitHubError, match="MAX_REPORT_BYTES"):
            client._gunzip_capped(blob)

    def test_allows_content_within_the_cap(self):
        import zlib

        payload = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        blob = payload.compress(b'{"a":1}') + payload.flush()

        assert _client()._gunzip_capped(blob) == b'{"a":1}'


class TestBackfillWindow:
    """The HTTP-facing validation, exercised through the same rules the handler applies."""

    def _span(self, since: date, until: date) -> int:
        return (until - since).days + 1

    def test_span_is_inclusive(self):
        assert self._span(date(2026, 8, 1), date(2026, 8, 1)) == 1
        assert self._span(date(2026, 8, 1), date(2026, 8, 7)) == 7

    def test_default_cap_rejects_a_multi_year_request(self):
        settings = Settings(max_backfill_days=90)
        span = self._span(date(2020, 1, 1), date(2026, 9, 1))
        assert span > settings.max_backfill_days

    def test_cap_is_configurable(self):
        assert Settings(max_backfill_days=365).max_backfill_days == 365

    def test_validate_rejects_a_zero_cap(self):
        with pytest.raises(ValueError, match="MAX_BACKFILL_DAYS"):
            Settings(
                github_orgs=["acme"], key_vault_name="kv",
                sql_connection_string="x", lake_account_name="lake",
                max_backfill_days=0,
            ).validate()

    def test_validate_rejects_an_empty_allowlist(self):
        with pytest.raises(ValueError, match="REPORT_HOST_ALLOWLIST"):
            Settings(
                github_orgs=["acme"], key_vault_name="kv",
                sql_connection_string="x", lake_account_name="lake",
                report_host_allowlist=(),
            ).validate()


class TestBackfillHandler:
    def _request(self, body):
        import azure.functions as func

        return func.HttpRequest(
            method="POST",
            url="/api/backfill",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )

    def test_rejects_malformed_dates_with_400(self):
        from function_app import ingest_backfill

        response = ingest_backfill(self._request({"since": "not-a-date"}))
        assert response.status_code == 400
        assert b"ISO dates" in response.get_body()

    def test_rejects_reversed_range(self):
        from function_app import ingest_backfill

        response = ingest_backfill(
            self._request({"since": "2026-08-10", "until": "2026-08-01"})
        )
        assert response.status_code == 400

    def test_rejects_future_until(self):
        from function_app import ingest_backfill

        future = (date.today() + timedelta(days=5)).isoformat()
        response = ingest_backfill(self._request({"until": future}))
        assert response.status_code == 400
        assert b"yesterday" in response.get_body()

    def test_rejects_oversized_window(self):
        from function_app import ingest_backfill

        response = ingest_backfill(
            self._request({"since": "2020-01-01", "until": "2026-01-01"})
        )
        assert response.status_code == 400
        assert b"MAX_BACKFILL_DAYS" in response.get_body()
