"""Flatten GitHub report payloads into tabular rows for the staging tables.

A per-user metrics record carries several parallel breakdown arrays:
    totals_by_ide[]           -> ide
    totals_by_model_feature[] -> model x feature (chat activity only)
    totals_by_feature[]       -> feature

They are siblings, not a cross-product: GitHub does not publish ide x model.
Each is flattened into its own table and recombined in SQL.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

# Observed `ide` dimension values, mapped to a friendlier label for reporting.
IDE_FAMILY = {
    "vscode": "VS Code",
    "visualstudio": "Visual Studio",
    "intellij": "JetBrains",
    "eclipse": "Eclipse",
    "xcode": "Xcode",
    "neovim": "Neovim",
    "vim": "Vim",
    "emacs": "Emacs",
    "zed": "Zed",
}


def ide_family(ide: str) -> str:
    return IDE_FAMILY.get((ide or "").strip().lower(), "Other")


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _as_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _counts(node: dict[str, Any]) -> dict[str, int]:
    return {
        "user_initiated_interaction_count": _as_int(node.get("user_initiated_interaction_count")),
        "code_generation_activity_count": _as_int(node.get("code_generation_activity_count")),
        "code_acceptance_activity_count": _as_int(node.get("code_acceptance_activity_count")),
        "loc_suggested_to_add_sum": _as_int(node.get("loc_suggested_to_add_sum")),
        "loc_suggested_to_delete_sum": _as_int(node.get("loc_suggested_to_delete_sum")),
        "loc_added_sum": _as_int(node.get("loc_added_sum")),
        "loc_deleted_sum": _as_int(node.get("loc_deleted_sum")),
    }


def flatten_user_day(records: Iterable[dict[str, Any]], org: str) -> list[dict[str, Any]]:
    """One row per user per day."""
    rows: list[dict[str, Any]] = []

    for record in records:
        day = record.get("day")
        login = record.get("user_login")
        if not day or not login:
            continue

        phase = record.get("ai_adoption_phase") or {}
        rows.append(
            {
                "activity_date": str(day)[:10],
                "user_login": login,
                "user_id": _as_int(record.get("user_id")),
                "org_login": org,
                "organization_id": str(record.get("organization_id") or ""),
                "enterprise_id": str(record.get("enterprise_id") or ""),
                # Consumption signal only - GitHub states this is not an invoicing total.
                "ai_credits_used": _as_decimal(record.get("ai_credits_used")),
                "adoption_phase": phase.get("phase") or "No Cohort",
                "adoption_phase_number": _as_int(phase.get("phase_number")),
                "used_agent": _bool(record.get("used_agent")),
                "used_chat": _bool(record.get("used_chat")),
                "used_cli": _bool(record.get("used_cli")),
                "used_copilot_app": _bool(record.get("used_copilot_app")),
                "used_copilot_cloud_agent": _bool(record.get("used_copilot_cloud_agent")),
                "used_code_review_active": _bool(record.get("used_copilot_code_review_active")),
                "used_code_review_passive": _bool(record.get("used_copilot_code_review_passive")),
                **_counts(record),
            }
        )

    return rows


def flatten_user_ide(records: Iterable[dict[str, Any]], org: str) -> list[dict[str, Any]]:
    """One row per user per day per IDE. This is what makes editor attribution per-user."""
    rows: list[dict[str, Any]] = []

    for record in records:
        day = record.get("day")
        login = record.get("user_login")
        if not day or not login:
            continue

        for entry in record.get("totals_by_ide") or []:
            ide = (entry.get("ide") or "unknown").strip().lower()
            ide_version = (entry.get("last_known_ide_version") or {}).get("ide_version")
            plugin = entry.get("last_known_plugin_version") or {}

            rows.append(
                {
                    "activity_date": str(day)[:10],
                    "user_login": login,
                    "org_login": org,
                    "ide": ide,
                    "ide_family": ide_family(ide),
                    "last_known_ide_version": ide_version or "",
                    "last_known_plugin_version": plugin.get("plugin_version") or "",
                    **_counts(entry),
                }
            )

    return rows


def flatten_user_model_feature(records: Iterable[dict[str, Any]], org: str) -> list[dict[str, Any]]:
    """One row per user per day per model per feature. Chat activity only, per GitHub."""
    rows: list[dict[str, Any]] = []

    for record in records:
        day = record.get("day")
        login = record.get("user_login")
        if not day or not login:
            continue

        for entry in record.get("totals_by_model_feature") or []:
            rows.append(
                {
                    "activity_date": str(day)[:10],
                    "user_login": login,
                    "org_login": org,
                    # 'auto' means auto-selection was used and no specific model was attributed.
                    "model_name": (entry.get("model") or "unknown").strip().lower(),
                    "feature": (entry.get("feature") or "others").strip().lower(),
                    **_counts(entry),
                }
            )

    return rows


def flatten_user_teams(records: Iterable[dict[str, Any]], org: str) -> list[dict[str, Any]]:
    """User-to-team membership. GitHub omits teams with fewer than 5 seated users."""
    rows: list[dict[str, Any]] = []

    for record in records:
        login = record.get("user_login")
        day = record.get("day")
        if not login or not day:
            continue

        rows.append(
            {
                "activity_date": str(day)[:10],
                "user_login": login,
                "user_id": _as_int(record.get("user_id")),
                "org_login": org,
                "team_id": _as_int(record.get("team_id")),
                "team_slug": record.get("slug") or "",
            }
        )

    return rows


def flatten_premium_requests(
    payload: dict[str, Any],
    org: str,
    user_login: str,
    usage_date: date,
) -> list[dict[str, Any]]:
    """Exact per-user spend. The date comes from the request period, not the line item."""
    rows: list[dict[str, Any]] = []

    for item in payload.get("usageItems") or []:
        gross = _as_decimal(item.get("grossAmount"))
        discount = _as_decimal(item.get("discountAmount"))
        net = item.get("netAmount")

        rows.append(
            {
                "usage_date": usage_date.isoformat(),
                "user_login": user_login,
                "org_login": org,
                "product": item.get("product") or "",
                "sku": item.get("sku") or "",
                # The billing payload carries a real model field - no SKU parsing needed.
                "model_name": (item.get("model") or "unknown").strip().lower(),
                "unit_type": item.get("unitType") or "",
                "price_per_unit": _as_decimal(item.get("pricePerUnit")),
                "gross_quantity": _as_decimal(item.get("grossQuantity")),
                "discount_quantity": _as_decimal(item.get("discountQuantity")),
                "net_quantity": _as_decimal(item.get("netQuantity")),
                "gross_amount": gross,
                "discount_amount": discount,
                "net_amount": _as_decimal(net) if net is not None else gross - discount,
            }
        )

    return rows


def flatten_seats(seats: Iterable[dict[str, Any]], org: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for seat in seats:
        assignee = seat.get("assignee") or {}
        team = seat.get("assigning_team") or {}
        rows.append(
            {
                "user_login": assignee.get("login") or "",
                "user_id": _as_int(assignee.get("id")),
                "org_login": org,
                "team_name": team.get("name") or "",
                "plan_type": seat.get("plan_type") or "",
                "created_at": seat.get("created_at"),
                "last_activity_at": seat.get("last_activity_at"),
                "last_activity_editor": seat.get("last_activity_editor") or "",
                "pending_cancellation_date": seat.get("pending_cancellation_date"),
            }
        )

    return rows


def as_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])
