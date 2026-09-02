import sys
from pathlib import Path

import pytest

# The Function App root is the import root at runtime; mirror that for tests.
FUNCTIONS_ROOT = Path(__file__).resolve().parents[1] / "src" / "functions"
sys.path.insert(0, str(FUNCTIONS_ROOT))


@pytest.fixture
def user_record():
    """A per-user metrics record shaped like GitHub's documented example schema."""
    return {
        "day": "2026-08-01",
        "user_id": 1001,
        "user_login": "jimmy",
        "enterprise_id": "1",
        "organization_id": "999",
        "ai_credits_used": 12.5,
        "user_initiated_interaction_count": 10,
        "code_generation_activity_count": 8,
        "code_acceptance_activity_count": 3,
        "loc_suggested_to_add_sum": 34,
        "loc_suggested_to_delete_sum": 6,
        "loc_added_sum": 32,
        "loc_deleted_sum": 6,
        "used_agent": False,
        "used_chat": True,
        "used_cli": True,
        "used_copilot_app": False,
        "used_copilot_cloud_agent": False,
        "used_copilot_code_review_active": None,
        "used_copilot_code_review_passive": None,
        "ai_adoption_phase": {"phase": "Phase 2", "phase_number": 2, "version": "v1"},
        "totals_by_ide": [
            {
                "ide": "vscode",
                "user_initiated_interaction_count": 7,
                "code_generation_activity_count": 5,
                "code_acceptance_activity_count": 2,
                "loc_added_sum": 20,
                "loc_deleted_sum": 4,
                "last_known_ide_version": {"ide_version": "1.85.0", "sampled_at": "2026-08-01T00:00:02Z"},
                "last_known_plugin_version": {"plugin": "copilot-chat", "plugin_version": "0.20.1"},
            },
            {
                "ide": "visualstudio",
                "user_initiated_interaction_count": 3,
                "code_generation_activity_count": 3,
                "code_acceptance_activity_count": 1,
                "loc_added_sum": 12,
                "loc_deleted_sum": 2,
            },
        ],
        "totals_by_model_feature": [
            {
                "model": "gpt-5.3",
                "feature": "chat_panel_agent_mode",
                "user_initiated_interaction_count": 6,
                "code_generation_activity_count": 4,
                "code_acceptance_activity_count": 2,
            },
            {
                "model": "auto",
                "feature": "chat_inline",
                "user_initiated_interaction_count": 4,
                "code_generation_activity_count": 4,
                "code_acceptance_activity_count": 1,
            },
        ],
    }


@pytest.fixture
def billing_payload():
    return {
        "timePeriod": {"year": 2026, "month": 8, "day": 1},
        "organization": "acme",
        "user": "jimmy",
        "usageItems": [
            {
                "product": "copilot",
                "sku": "premium_request",
                "model": "GPT-5.3",
                "unitType": "request",
                "pricePerUnit": 0.04,
                "grossQuantity": 1250,
                "grossAmount": 50.0,
                "discountQuantity": 0,
                "discountAmount": 0.0,
                "netQuantity": 1250,
                "netAmount": 50.0,
            }
        ],
    }
