"""GitHub API client for Copilot usage metrics, premium-request billing, and seats.

The metrics API does not return data inline. It returns pre-signed `download_links`
pointing at NDJSON files, which this client fetches and parses. Billing dollars are
only attributable per user by passing a `user` filter, so spend is pulled one user
at a time.
"""
from __future__ import annotations

import json
import logging
import time
import zlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import jwt
import requests
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from .config import Settings

API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
LOGGER = logging.getLogger(__name__)

# Reports are normally precomputed, but a cold report can return 202 while it builds.
POLL_INTERVAL_SECONDS = 5
POLL_MAX_ATTEMPTS = 24
MAX_RETRIES = 5
DOWNLOAD_TIMEOUT_SECONDS = 300
MAX_REDIRECTS = 3
CHUNK_BYTES = 65536
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class GitHubError(RuntimeError):
    """Raised when the GitHub API returns an unrecoverable response."""


class GitHubClient:
    def __init__(self, settings: Settings, credential: DefaultAzureCredential | None = None):
        self._settings = settings
        self._credential = credential or DefaultAzureCredential()
        self._session = requests.Session()
        self._token: str | None = None
        self._token_expires_at = datetime.min.replace(tzinfo=UTC)

    # ------------------------------------------------------------------ auth

    def _read_secret(self) -> str:
        client = SecretClient(vault_url=self._settings.key_vault_uri, credential=self._credential)
        secret = client.get_secret(self._settings.github_credential_secret_name)
        if not secret.value:
            raise GitHubError("GitHub credential secret is empty.")
        return secret.value

    def _installation_token(self, private_key: str) -> tuple[str, datetime]:
        now = int(time.time())
        assertion = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self._settings.github_app_id},
            private_key,
            algorithm="RS256",
        )
        url = f"{API_ROOT}/app/installations/{self._settings.github_app_installation_id}/access_tokens"
        response = self._session.post(
            url,
            headers={
                "Authorization": f"Bearer {assertion}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=30,
        )
        if response.status_code != 201:
            raise GitHubError(f"Failed to mint installation token: {response.status_code} {response.text}")
        payload = response.json()
        expires = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
        return payload["token"], expires

    def _auth_token(self) -> str:
        if self._token and datetime.now(UTC) < self._token_expires_at - timedelta(minutes=5):
            return self._token

        secret = self._read_secret()
        if self._settings.uses_github_app:
            self._token, self._token_expires_at = self._installation_token(secret)
        else:
            # PATs do not expose an expiry via the API; re-read hourly so rotation is picked up.
            self._token = secret
            self._token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "copilot-metrics-accelerator",
        }

    # ----------------------------------------------------------------- fetch

    def _request(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        for attempt in range(1, MAX_RETRIES + 1):
            response = self._session.get(url, headers=self._headers(), params=params, timeout=120)

            if response.status_code == 202:
                for _ in range(POLL_MAX_ATTEMPTS):
                    time.sleep(POLL_INTERVAL_SECONDS)
                    response = self._session.get(url, headers=self._headers(), params=params, timeout=120)
                    if response.status_code != 202:
                        break
                else:
                    raise GitHubError(f"Report at {url} was still building after polling timed out.")

            if response.status_code in (403, 429):
                retry_after = int(response.headers.get("Retry-After", 0))
                remaining = response.headers.get("X-RateLimit-Remaining")
                if retry_after or remaining == "0":
                    delay = retry_after or 60
                    LOGGER.warning("Rate limited on %s; sleeping %ss (attempt %s)", url, delay, attempt)
                    time.sleep(delay)
                    continue

            if response.status_code >= 500:
                delay = 2**attempt
                LOGGER.warning("Server error %s on %s; retrying in %ss", response.status_code, url, delay)
                time.sleep(delay)
                continue

            if not response.ok:
                raise GitHubError(f"GET {url} failed: {response.status_code} {response.text}")

            return response

        raise GitHubError(f"GET {url} exhausted {MAX_RETRIES} retries.")

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        next_url: str | None = url

        while next_url:
            response = self._request(next_url, params)
            payload = response.json()

            if isinstance(payload, list):
                yield from payload
            else:
                yield payload

            next_url = response.links.get("next", {}).get("url")
            params = None  # the Link header already carries the cursor

    # ------------------------------------------------------- metrics reports

    def _check_download_url(self, url: str) -> None:
        """Reject links that would turn the report download into an SSRF primitive."""
        parsed = urlparse(url)

        if parsed.scheme != "https":
            raise GitHubError(f"Refusing report link with scheme '{parsed.scheme}'; https required.")

        host = (parsed.hostname or "").lower()
        allowed = self._settings.report_host_allowlist
        if not any(host == entry or host.endswith(f".{entry}") for entry in allowed):
            raise GitHubError(
                f"Refusing report link from unexpected host '{host}'. "
                "Add it to REPORT_HOST_ALLOWLIST if it is legitimate."
            )

    def _read_capped(self, response: requests.Response) -> bytes:
        limit = self._settings.max_report_bytes
        chunks: list[bytes] = []
        total = 0

        for chunk in response.iter_content(CHUNK_BYTES):
            total += len(chunk)
            if total > limit:
                raise GitHubError(f"Report download exceeded MAX_REPORT_BYTES ({limit}).")
            chunks.append(chunk)

        return b"".join(chunks)

    def _fetch_download(self, url: str) -> bytes:
        """Follow redirects manually so every hop is validated, not just the first."""
        current = url

        for _ in range(MAX_REDIRECTS + 1):
            self._check_download_url(current)
            # Pre-signed links carry their own auth; our header would invalidate them.
            response = self._session.get(
                current,
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
                stream=True,
                allow_redirects=False,
            )

            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise GitHubError("Report link redirected without a Location header.")
                # Location is frequently relative; resolve it before validating the next hop.
                current = urljoin(current, location)
                continue

            with response:
                if response.status_code in REDIRECT_CODES:
                    raise GitHubError(
                        f"Report link returned {response.status_code} with no usable Location."
                    )
                if not response.ok:
                    raise GitHubError(f"Report download failed with {response.status_code}.")
                return self._read_capped(response)

        raise GitHubError(f"Report link exceeded {MAX_REDIRECTS} redirects.")

    def _download_ndjson(self, url: str) -> list[dict[str, Any]]:
        body = self._fetch_download(url)

        if body[:2] == b"\x1f\x8b":
            body = self._gunzip_capped(body)

        rows: list[dict[str, Any]] = []
        malformed = 0

        for line in body.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(parsed, list):
                rows.extend(parsed)
            else:
                rows.append(parsed)

        if malformed:
            # Tolerate the odd bad line, but a mostly-bad file means the format changed.
            LOGGER.warning("Skipped %s malformed NDJSON lines from a report file.", malformed)
            if malformed > len(rows):
                raise GitHubError("Report file was mostly unparseable; the format may have changed.")

        return rows

    def _gunzip_capped(self, body: bytes) -> bytes:
        """Bounded decompression: a small archive can otherwise expand without limit."""
        limit = self._settings.max_report_bytes
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = decompressor.decompress(body, limit)

        if decompressor.unconsumed_tail:
            raise GitHubError(f"Report expanded beyond MAX_REPORT_BYTES ({limit}).")
        if not decompressor.eof:
            raise GitHubError("Report archive ended unexpectedly; the download may be truncated.")

        return out

    def fetch_report(self, report: str, day: date, org: str | None = None) -> list[dict[str, Any]]:
        """Resolve a report's download links for a day, then fetch and parse them."""
        scope = f"/orgs/{org}" if org else f"/enterprises/{self._settings.github_enterprise}"
        url = f"{API_ROOT}{scope}/copilot/metrics/reports/{report}"

        response = self._request(url, {"day": day.isoformat()})
        if response.status_code == 204 or not response.content:
            LOGGER.info("Report %s for %s returned no content.", report, day)
            return []

        payload = response.json()
        links = payload.get("download_links") or []
        if not links:
            LOGGER.warning("Report %s for %s returned no download links.", report, day)
            return []

        rows: list[dict[str, Any]] = []
        for link in links:
            rows.extend(self._download_ndjson(link))

        LOGGER.info("Report %s for %s yielded %s rows.", report, day, len(rows))
        return rows

    def user_metrics(self, day: date, org: str | None = None) -> list[dict[str, Any]]:
        """One record per user per day, including totals_by_ide and totals_by_model_feature."""
        return self.fetch_report("users-1-day", day, org)

    def user_teams(self, day: date, org: str | None = None) -> list[dict[str, Any]]:
        """User-to-team map. Teams with fewer than 5 seated users are omitted by GitHub."""
        return self.fetch_report("user-teams-1-day", day, org)

    def aggregate_metrics(self, day: date, org: str | None = None) -> list[dict[str, Any]]:
        return self.fetch_report("organization-1-day" if org else "enterprise-1-day", day, org)

    # --------------------------------------------------------------- billing

    def premium_request_usage(
        self,
        org: str,
        user: str,
        year: int,
        month: int,
        day: int | None = None,
    ) -> dict[str, Any]:
        """Per-user premium request spend. The `user` filter is what makes it attributable."""
        url = f"{API_ROOT}/organizations/{org}/settings/billing/premium_request/usage"
        params: dict[str, Any] = {"year": year, "month": month, "user": user}
        if day is not None:
            params["day"] = day

        response = self._request(url, params)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def org_billing_usage(self, org: str, year: int, month: int, day: int | None = None) -> list[dict[str, Any]]:
        """Org-wide usage line items. Carries a date but no user attribution."""
        url = f"{API_ROOT}/organizations/{org}/settings/billing/usage"
        params: dict[str, Any] = {"year": year, "month": month}
        if day is not None:
            params["day"] = day

        items: list[dict[str, Any]] = []
        for page in self._paginate(url, params):
            if isinstance(page, dict) and "usageItems" in page:
                items.extend(page["usageItems"] or [])
        return items

    # ----------------------------------------------------------------- seats

    def copilot_seats(self, org: str) -> list[dict[str, Any]]:
        url = f"{API_ROOT}/orgs/{org}/copilot/billing/seats"
        seats: list[dict[str, Any]] = []
        for page in self._paginate(url):
            if isinstance(page, dict) and "seats" in page:
                seats.extend(page["seats"] or [])
        return seats
