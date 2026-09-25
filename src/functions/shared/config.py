"""Runtime configuration resolved from app settings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _flag(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# Report download links are pre-signed URLs supplied by the API response. Restricting
# the host stops a tampered or unexpected response turning the Function into an SSRF
# proxy. Confirm the real host with scripts/validate_github_api.py and extend if needed.
_DEFAULT_REPORT_HOSTS = (
    "github.com",
    "githubusercontent.com",
    "githubassets.com",
    "blob.core.windows.net",
)


@dataclass(frozen=True)
class Settings:
    github_enterprise: str = ""
    github_orgs: list[str] = field(default_factory=list)

    # Metrics reports publish at either enterprise or organization scope. Enterprise
    # covers every org in one pull; organization is the fallback when the caller
    # only holds org-level permissions.
    metrics_scope: str = "enterprise"

    # Per-user billing needs one request per user per day, so seat count and the
    # reload window together set the request volume. See docs/architecture.md.
    max_billing_users: int = 2000

    # Escape hatch: disable if a driver version mishandles bulk parameter binding.
    sql_fast_executemany: bool = True

    key_vault_name: str = ""
    github_credential_secret_name: str = "github-credential"  # noqa: S105 - secret name, not a secret
    github_app_id: str = ""
    github_app_installation_id: str = ""
    lake_account_name: str = ""
    lake_filesystem_name: str = "raw"
    fabric_warehouse_connection_string: str = ""
    backfill_days: int = 28
    reload_trailing_days: int = 7

    # Caps the range a single backfill request may ask for. Without it one call can
    # queue months of per-user billing requests and hold the ingestion lock throughout.
    max_backfill_days: int = 90

    report_host_allowlist: tuple[str, ...] = _DEFAULT_REPORT_HOSTS
    max_report_bytes: int = 67_108_864

    # Fail the run when a report yields zero rows rather than silently loading nothing.
    fail_on_empty_report: bool = True

    @property
    def key_vault_uri(self) -> str:
        return f"https://{self.key_vault_name}.vault.azure.net"

    @property
    def lake_url(self) -> str:
        return f"https://{self.lake_account_name}.dfs.core.windows.net"

    @property
    def uses_github_app(self) -> bool:
        return bool(self.github_app_id and self.github_app_installation_id)

    @property
    def use_enterprise_metrics(self) -> bool:
        return self.metrics_scope == "enterprise" and bool(self.github_enterprise)

    def validate(self) -> None:
        problems: list[str] = []

        if not self.github_enterprise and not self.github_orgs:
            problems.append("Set GITHUB_ENTERPRISE or GITHUB_ORGS.")
        if self.metrics_scope not in ("enterprise", "organization"):
            problems.append("METRICS_SCOPE must be 'enterprise' or 'organization'.")
        if not self.use_enterprise_metrics and not self.github_orgs:
            problems.append("Organization-scope metrics require GITHUB_ORGS.")
        if not self.github_orgs:
            problems.append("GITHUB_ORGS is required for seats and billing.")
        if not self.key_vault_name:
            problems.append("KEY_VAULT_NAME is required.")
        if not self.fabric_warehouse_connection_string:
            problems.append("FABRIC_WAREHOUSE_CONNECTION_STRING is required.")
        if not self.lake_account_name:
            problems.append("LAKE_ACCOUNT_NAME is required.")
        if self.max_backfill_days < 1:
            problems.append("MAX_BACKFILL_DAYS must be at least 1.")
        if not self.report_host_allowlist:
            problems.append("REPORT_HOST_ALLOWLIST must not be empty.")

        if problems:
            raise ValueError("Invalid configuration: " + " ".join(problems))


def load_settings() -> Settings:
    return Settings(
        github_enterprise=os.environ.get("GITHUB_ENTERPRISE", "").strip(),
        github_orgs=_split_csv(os.environ.get("GITHUB_ORGS", "")),
        metrics_scope=os.environ.get("METRICS_SCOPE", "enterprise").strip().lower(),
        max_billing_users=int(os.environ.get("MAX_BILLING_USERS", "2000")),
        key_vault_name=os.environ.get("KEY_VAULT_NAME", ""),
        github_credential_secret_name=os.environ.get("GITHUB_CREDENTIAL_SECRET_NAME", "github-credential"),
        github_app_id=os.environ.get("GITHUB_APP_ID", ""),
        github_app_installation_id=os.environ.get("GITHUB_APP_INSTALLATION_ID", ""),
        lake_account_name=os.environ.get("LAKE_ACCOUNT_NAME", ""),
        lake_filesystem_name=os.environ.get("LAKE_FILESYSTEM_NAME", "raw"),
        fabric_warehouse_connection_string=os.environ.get(
            "FABRIC_WAREHOUSE_CONNECTION_STRING", ""
        ),
        sql_fast_executemany=_flag(os.environ.get("SQL_FAST_EXECUTEMANY", "true"), True),
        backfill_days=int(os.environ.get("BACKFILL_DAYS", "28")),
        reload_trailing_days=int(os.environ.get("RELOAD_TRAILING_DAYS", "7")),
        max_backfill_days=int(os.environ.get("MAX_BACKFILL_DAYS", "90")),
        report_host_allowlist=tuple(_split_csv(os.environ.get("REPORT_HOST_ALLOWLIST", "")))
        or _DEFAULT_REPORT_HOSTS,
        max_report_bytes=int(os.environ.get("MAX_REPORT_BYTES", str(67_108_864))),
        fail_on_empty_report=_flag(os.environ.get("FAIL_ON_EMPTY_REPORT", "true"), True),
    )
