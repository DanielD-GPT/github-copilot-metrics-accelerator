"""Tests for the payload flatteners.

These guard the field names and dimension values that the whole attribution model
depends on. An earlier revision silently produced zero rows because it matched
`visual_studio` instead of `visualstudio`, so those mappings are asserted explicitly.
"""
from datetime import date
from decimal import Decimal

from shared.transform import (
    flatten_premium_requests,
    flatten_seats,
    flatten_user_day,
    flatten_user_ide,
    flatten_user_model_feature,
    flatten_user_teams,
    ide_family,
)


class TestIdeFamily:
    def test_documented_values_map(self):
        assert ide_family("vscode") == "VS Code"
        assert ide_family("visualstudio") == "Visual Studio"
        assert ide_family("intellij") == "JetBrains"
        assert ide_family("xcode") == "Xcode"
        assert ide_family("neovim") == "Neovim"

    def test_is_case_insensitive(self):
        assert ide_family("VSCode") == "VS Code"

    def test_unknown_falls_back(self):
        assert ide_family("someeditor") == "Other"
        assert ide_family("") == "Other"
        assert ide_family(None) == "Other"

    def test_legacy_guesses_are_not_used(self):
        # Regression: these were the wrong guesses that matched nothing.
        assert ide_family("visual_studio") == "Other"
        assert ide_family("jetbrains") == "Other"


class TestFlattenUserDay:
    def test_extracts_core_fields(self, user_record):
        rows = flatten_user_day([user_record], "acme")
        assert len(rows) == 1
        row = rows[0]
        assert row["user_login"] == "jimmy"
        assert row["activity_date"] == "2026-08-01"
        assert row["org_login"] == "acme"
        assert row["ai_credits_used"] == Decimal("12.5")
        assert row["adoption_phase"] == "Phase 2"
        assert row["adoption_phase_number"] == 2

    def test_booleans_become_bits_and_none_survives(self, user_record):
        row = flatten_user_day([user_record], "acme")[0]
        assert row["used_chat"] == 1
        assert row["used_agent"] == 0
        # Null means "no signal", which is different from False.
        assert row["used_code_review_active"] is None

    def test_skips_records_missing_identity(self):
        assert flatten_user_day([{"day": "2026-08-01"}], "acme") == []
        assert flatten_user_day([{"user_login": "x"}], "acme") == []

    def test_empty_input(self):
        assert flatten_user_day([], "acme") == []


class TestFlattenUserIde:
    def test_one_row_per_ide(self, user_record):
        rows = flatten_user_ide([user_record], "acme")
        assert len(rows) == 2
        assert {r["ide"] for r in rows} == {"vscode", "visualstudio"}

    def test_maps_family_and_versions(self, user_record):
        rows = {r["ide"]: r for r in flatten_user_ide([user_record], "acme")}
        assert rows["visualstudio"]["ide_family"] == "Visual Studio"
        assert rows["vscode"]["last_known_ide_version"] == "1.85.0"
        assert rows["vscode"]["last_known_plugin_version"] == "0.20.1"

    def test_missing_version_objects_are_blank_not_none(self, user_record):
        rows = {r["ide"]: r for r in flatten_user_ide([user_record], "acme")}
        assert rows["visualstudio"]["last_known_ide_version"] == ""

    def test_weights_are_preserved_for_allocation(self, user_record):
        rows = {r["ide"]: r for r in flatten_user_ide([user_record], "acme")}
        assert rows["vscode"]["user_initiated_interaction_count"] == 7
        assert rows["visualstudio"]["user_initiated_interaction_count"] == 3

    def test_absent_array_yields_nothing(self):
        record = {"day": "2026-08-01", "user_login": "jimmy"}
        assert flatten_user_ide([record], "acme") == []


class TestFlattenUserModelFeature:
    def test_one_row_per_model_feature(self, user_record):
        rows = flatten_user_model_feature([user_record], "acme")
        assert len(rows) == 2
        assert {(r["model_name"], r["feature"]) for r in rows} == {
            ("gpt-5.3", "chat_panel_agent_mode"),
            ("auto", "chat_inline"),
        }

    def test_model_is_lowercased_for_join_stability(self):
        record = {
            "day": "2026-08-01",
            "user_login": "jimmy",
            "totals_by_model_feature": [{"model": "GPT-5.3", "feature": "chat_inline"}],
        }
        assert flatten_user_model_feature([record], "acme")[0]["model_name"] == "gpt-5.3"

    def test_missing_model_becomes_unknown(self):
        record = {
            "day": "2026-08-01",
            "user_login": "jimmy",
            "totals_by_model_feature": [{"feature": "chat_inline"}],
        }
        row = flatten_user_model_feature([record], "acme")[0]
        assert row["model_name"] == "unknown"

    def test_missing_feature_becomes_others(self):
        record = {
            "day": "2026-08-01",
            "user_login": "jimmy",
            "totals_by_model_feature": [{"model": "gpt-5.3"}],
        }
        assert flatten_user_model_feature([record], "acme")[0]["feature"] == "others"


class TestFlattenPremiumRequests:
    def test_carries_user_and_model(self, billing_payload):
        rows = flatten_premium_requests(billing_payload, "acme", "jimmy", date(2026, 8, 1))
        assert len(rows) == 1
        row = rows[0]
        # The whole point: billing is only attributable because we filtered by user.
        assert row["user_login"] == "jimmy"
        assert row["model_name"] == "gpt-5.3"
        assert row["net_amount"] == Decimal("50.0")

    def test_date_comes_from_the_request_period(self, billing_payload):
        rows = flatten_premium_requests(billing_payload, "acme", "jimmy", date(2026, 8, 15))
        assert rows[0]["usage_date"] == "2026-08-15"

    def test_net_amount_falls_back_to_gross_minus_discount(self):
        payload = {"usageItems": [{"product": "copilot", "grossAmount": 10, "discountAmount": 2.5}]}
        row = flatten_premium_requests(payload, "acme", "jimmy", date(2026, 8, 1))[0]
        assert row["net_amount"] == Decimal("7.5")

    def test_zero_and_missing_amounts_are_safe(self):
        payload = {"usageItems": [{"product": "copilot"}]}
        row = flatten_premium_requests(payload, "acme", "jimmy", date(2026, 8, 1))[0]
        assert row["net_amount"] == Decimal("0")
        assert row["price_per_unit"] == Decimal("0")

    def test_empty_payload(self):
        assert flatten_premium_requests({}, "acme", "jimmy", date(2026, 8, 1)) == []


class TestFlattenUserTeams:
    def test_maps_team_identity(self):
        rows = flatten_user_teams(
            [{"user_id": 1001, "user_login": "octocat", "day": "2026-05-14", "team_id": 42, "slug": "frontend"}],
            "acme",
        )
        assert rows[0]["team_id"] == 42
        assert rows[0]["team_slug"] == "frontend"

    def test_user_in_multiple_teams_yields_multiple_rows(self):
        records = [
            {"user_id": 1, "user_login": "octocat", "day": "2026-05-14", "team_id": 42, "slug": "frontend"},
            {"user_id": 1, "user_login": "octocat", "day": "2026-05-14", "team_id": 43, "slug": "backend"},
        ]
        assert len(flatten_user_teams(records, "acme")) == 2


class TestFlattenSeats:
    def test_extracts_assignee(self):
        seats = [
            {
                "assignee": {"login": "jimmy", "id": 1001},
                "assigning_team": {"name": "Platform"},
                "plan_type": "business",
                "last_activity_editor": "vscode",
            }
        ]
        row = flatten_seats(seats, "acme")[0]
        assert row["user_login"] == "jimmy"
        assert row["user_id"] == 1001
        assert row["team_name"] == "Platform"

    def test_seat_without_assignee_is_blank_not_crash(self):
        rows = flatten_seats([{"plan_type": "business"}], "acme")
        assert rows[0]["user_login"] == ""
