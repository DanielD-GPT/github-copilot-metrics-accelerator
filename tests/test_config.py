"""Config validation and date-window helpers."""
import pytest

from shared.config import Settings, _flag, _split_csv


class TestHelpers:
    def test_split_csv_trims_and_drops_blanks(self):
        assert _split_csv("a, b ,, c ") == ["a", "b", "c"]
        assert _split_csv("") == []

    def test_flag_parsing(self):
        assert _flag("true") is True
        assert _flag("YES") is True
        assert _flag("1") is True
        assert _flag("false") is False
        assert _flag("", default=True) is True


class TestValidate:
    def _valid(self, **overrides):
        base = dict(
            github_orgs=["acme"],
            key_vault_name="kv",
            sql_connection_string="Driver=...",
            lake_account_name="lake",
        )
        base.update(overrides)
        return Settings(**base)

    def test_accepts_a_complete_config(self):
        self._valid().validate()

    def test_requires_orgs(self):
        # Both metrics and billing are pulled per org, so this is not optional.
        with pytest.raises(ValueError, match="GITHUB_ORGS"):
            self._valid(github_orgs=[], github_enterprise="ent").validate()

    def test_rejects_unknown_metrics_scope(self):
        with pytest.raises(ValueError, match="METRICS_SCOPE"):
            self._valid(metrics_scope="galaxy").validate()

    def test_requires_azure_targets(self):
        with pytest.raises(ValueError, match="SQL_CONNECTION_STRING"):
            self._valid(sql_connection_string="").validate()
        with pytest.raises(ValueError, match="LAKE_ACCOUNT_NAME"):
            self._valid(lake_account_name="").validate()

    def test_empty_config_reports_problems(self):
        with pytest.raises(ValueError):
            Settings().validate()


class TestDerivedProperties:
    def test_key_vault_and_lake_uris(self):
        s = Settings(key_vault_name="kv1", lake_account_name="lake1")
        assert s.key_vault_uri == "https://kv1.vault.azure.net"
        assert s.lake_url == "https://lake1.dfs.core.windows.net"

    def test_github_app_detection(self):
        assert Settings().uses_github_app is False
        assert Settings(github_app_id="1", github_app_installation_id="2").uses_github_app is True

    def test_enterprise_metrics_requires_a_slug(self):
        assert Settings(metrics_scope="enterprise").use_enterprise_metrics is False
        assert Settings(metrics_scope="enterprise", github_enterprise="e").use_enterprise_metrics is True
