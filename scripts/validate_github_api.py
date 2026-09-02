#!/usr/bin/env python3
"""Read-only probe of every GitHub endpoint this accelerator depends on.

Confirms the endpoints exist, the caller has the right permissions, and the payload
fields the ingestion code reads are actually present. Makes no writes, deploys
nothing, and requires no Azure resources.

Usage:
    export GITHUB_TOKEN=...            # never pass the token as an argument
    python scripts/validate_github_api.py --org my-org

    # include an enterprise-scope check
    python scripts/validate_github_api.py --org my-org --enterprise my-enterprise

Output is redacted by default: user logins are hashed and dollar amounts are reduced
to "present"/"absent", so the report can be shared without exposing personal data.
Pass --show-values only when running against your own account.

Exit code 0 = every required check passed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
TIMEOUT = 120

# Fields the ingestion code reads. Keep in sync with src/functions/shared/transform.py.
EXPECTED = {
    "user_metrics": [
        "day", "user_id", "user_login", "ai_credits_used",
        "user_initiated_interaction_count", "code_generation_activity_count",
        "code_acceptance_activity_count", "loc_added_sum",
        "totals_by_ide", "totals_by_model_feature", "ai_adoption_phase",
    ],
    "totals_by_ide[]": ["ide", "user_initiated_interaction_count", "code_generation_activity_count"],
    "totals_by_model_feature[]": ["model", "feature", "user_initiated_interaction_count"],
    "user_teams": ["user_id", "user_login", "day", "team_id", "slug"],
    "premium_request_usage": ["usageItems"],
    "usageItems[]": ["product", "sku", "model", "netAmount", "grossAmount", "netQuantity"],
    "seats": ["assignee"],
}

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


class Result:
    def __init__(self) -> None:
        self.checks: list[tuple[str, str, str]] = []
        self.failed = 0

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append((name, status, detail))
        if status == "FAIL":
            self.failed += 1

    def render(self, colour: bool) -> None:
        print()
        print("=" * 74)
        print("GitHub API validation")
        print("=" * 74)
        for name, status, detail in self.checks:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "SKIP": "SKIP"}[status]
            if colour:
                tone = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW, "SKIP": ""}[status]
                mark = f"{tone}{mark}{RESET}"
            print(f"[{mark}] {name}")
            if detail:
                for line in detail.splitlines():
                    print(f"       {line}")
        print("=" * 74)
        print(f"{len(self.checks)} checks, {self.failed} failed")


def redact_login(login: str, show: bool) -> str:
    if show:
        return login
    return "user-" + hashlib.sha256(login.encode()).hexdigest()[:8]


def request(path: str, token: str, params: dict | None = None) -> tuple[int, bytes, str]:
    url = f"{API_ROOT}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("User-Agent", "copilot-metrics-accelerator-validator")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.status, response.read(), ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        return exc.code, b"", body
    except Exception as exc:  # network, DNS, TLS
        return 0, b"", str(exc)


def download(url: str) -> list[dict]:
    """Report links are pre-signed; deliberately sent without an Authorization header."""
    req = urllib.request.Request(url, headers={"User-Agent": "copilot-metrics-accelerator-validator"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        body = response.read()

    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)

    rows: list[dict] = []
    for line in body.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        rows.extend(parsed) if isinstance(parsed, list) else rows.append(parsed)
    return rows


def field_report(sample: dict, expected: list[str]) -> tuple[list[str], list[str]]:
    present = [f for f in expected if f in sample]
    missing = [f for f in expected if f not in sample]
    return present, missing


def check_auth(result: Result, token: str) -> None:
    status, body, err = request("/rate_limit", token)
    if status != 200:
        result.add("Authentication", "FAIL", f"HTTP {status}. {err}")
        return

    core = json.loads(body).get("resources", {}).get("core", {})
    result.add(
        "Authentication",
        "PASS",
        f"rate limit {core.get('remaining')}/{core.get('limit')} remaining",
    )


def check_metrics_report(
    result: Result, token: str, report: str, scope_path: str, day: date, expected_key: str
) -> list[dict]:
    label = f"Metrics report: {report}"
    status, body, err = request(f"{scope_path}/copilot/metrics/reports/{report}", token, {"day": day.isoformat()})

    if status == 404:
        result.add(label, "FAIL", "404 - endpoint not found, or the Copilot usage metrics policy is disabled.")
        return []
    if status == 403:
        result.add(label, "FAIL", f"403 - token lacks permission. {err}")
        return []
    if status == 204 or not body:
        result.add(label, "WARN", f"No content for {day}. Try an earlier --day.")
        return []
    if status != 200:
        result.add(label, "FAIL", f"HTTP {status}. {err}")
        return []

    payload = json.loads(body)
    links = payload.get("download_links") or []
    if not links:
        result.add(label, "FAIL", f"200 OK but no download_links. Keys returned: {sorted(payload)}")
        return []

    try:
        rows = download(links[0])
    except Exception as exc:
        result.add(label, "FAIL", f"Could not download report file: {exc}")
        return []

    if not rows:
        result.add(label, "WARN", "Report downloaded but contained no records.")
        return []

    present, missing = field_report(rows[0], EXPECTED[expected_key])
    host = urllib.parse.urlparse(links[0]).hostname or "?"
    detail = (
        f"{len(links)} link(s), {len(rows)} records\n"
        f"download host: {host}   <- must appear in REPORT_HOST_ALLOWLIST\n"
        f"fields present: {len(present)}/{len(present) + len(missing)}"
    )
    if missing:
        detail += f"\nMISSING: {missing}"
        result.add(label, "FAIL", detail)
    else:
        result.add(label, "PASS", detail)
    return rows


def check_breakdowns(result: Result, rows: list[dict], show_values: bool) -> None:
    if not rows:
        result.add("Per-user breakdown arrays", "SKIP", "No user records to inspect.")
        return

    with_ide = next((r for r in rows if r.get("totals_by_ide")), None)
    with_mf = next((r for r in rows if r.get("totals_by_model_feature")), None)

    if not with_ide:
        result.add(
            "totals_by_ide",
            "FAIL",
            "No record carried a non-empty totals_by_ide. Editor attribution is impossible without it.",
        )
    else:
        entry = with_ide["totals_by_ide"][0]
        present, missing = field_report(entry, EXPECTED["totals_by_ide[]"])
        ides = sorted({e.get("ide", "?") for r in rows for e in (r.get("totals_by_ide") or [])})
        detail = f"observed ide values: {ides}"
        if missing:
            result.add("totals_by_ide", "FAIL", f"{detail}\nMISSING: {missing}")
        else:
            result.add("totals_by_ide", "PASS", detail)

    if not with_mf:
        result.add(
            "totals_by_model_feature",
            "WARN",
            "No record carried model x feature data. Chat activity may be absent for this day.",
        )
    else:
        entry = with_mf["totals_by_model_feature"][0]
        present, missing = field_report(entry, EXPECTED["totals_by_model_feature[]"])
        models = sorted({e.get("model", "?") for r in rows for e in (r.get("totals_by_model_feature") or [])})
        features = sorted({e.get("feature", "?") for r in rows for e in (r.get("totals_by_model_feature") or [])})
        detail = f"observed models: {models}\nobserved features: {features}"
        if missing:
            result.add("totals_by_model_feature", "FAIL", f"{detail}\nMISSING: {missing}")
        else:
            result.add("totals_by_model_feature", "PASS", detail)

    # The accelerator's central assumption: these arrays are siblings, never a cross-product.
    cross = any("model" in e and "ide" in e for r in rows for e in (r.get("totals_by_ide") or []))
    result.add(
        "ide x model cross-product",
        "WARN" if not cross else "PASS",
        "Not published - editor split stays modelled (expected)."
        if not cross
        else "A combined ide+model breakdown exists. Attribution could be made exact.",
    )


def check_billing(result: Result, token: str, org: str, login: str, day: date, show_values: bool) -> None:
    label = "Billing: premium_request usage (per user)"
    status, body, err = request(
        f"/organizations/{org}/settings/billing/premium_request/usage",
        token,
        {"year": day.year, "month": day.month, "day": day.day, "user": login},
    )

    if status == 404:
        result.add(label, "FAIL", "404 - enhanced billing platform may not be enabled for this org.")
        return
    if status == 403:
        result.add(label, "FAIL", f"403 - token needs org admin or billing manager. {err}")
        return
    if status != 200 or not body:
        result.add(label, "FAIL", f"HTTP {status}. {err}")
        return

    payload = json.loads(body)
    items = payload.get("usageItems") or []
    who = redact_login(login, show_values)

    if not items:
        result.add(label, "WARN", f"200 OK for {who} on {day}, but no usage items. Try a busier date.")
        return

    present, missing = field_report(items[0], EXPECTED["usageItems[]"])
    has_model = "model" in items[0]
    detail = f"user {who}, {len(items)} usage item(s)\nmodel field: {'PRESENT' if has_model else 'ABSENT'}"
    if show_values:
        detail += f"\nsample: {json.dumps(items[0])[:200]}"
    else:
        detail += f"\nnetAmount: {'present' if items[0].get('netAmount') is not None else 'absent'}"

    if missing:
        result.add(label, "FAIL", f"{detail}\nMISSING: {missing}")
    else:
        result.add(label, "PASS", detail)


def check_seats(result: Result, token: str, org: str, show_values: bool) -> list[str]:
    status, body, err = request(f"/orgs/{org}/copilot/billing/seats", token, {"per_page": 100})
    if status != 200 or not body:
        result.add("Copilot seats", "FAIL", f"HTTP {status}. {err}")
        return []

    seats = json.loads(body).get("seats") or []
    logins = [s.get("assignee", {}).get("login") for s in seats if s.get("assignee")]
    logins = [x for x in logins if x]

    if not logins:
        result.add("Copilot seats", "FAIL", "No seats returned; billing cannot be attributed to users.")
        return []

    result.add("Copilot seats", "PASS", f"{len(logins)} seat(s) on first page")
    return logins


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", required=True, help="GitHub organization slug")
    parser.add_argument("--enterprise", help="Optional enterprise slug for enterprise-scope checks")
    parser.add_argument("--day", help="Day to probe (YYYY-MM-DD). Defaults to 3 days ago.")
    parser.add_argument("--user", help="Specific login for the billing check. Defaults to the first seat.")
    parser.add_argument("--show-values", action="store_true", help="Do not redact logins and amounts")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit machine-readable output")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN is not set. Export it rather than passing it as an argument.", file=sys.stderr)
        return 2

    day = date.fromisoformat(args.day) if args.day else date.today() - timedelta(days=3)
    result = Result()

    check_auth(result, token)

    org_scope = f"/orgs/{args.org}"
    rows = check_metrics_report(result, token, "users-1-day", org_scope, day, "user_metrics")
    check_breakdowns(result, rows, args.show_values)

    teams = check_metrics_report(result, token, "user-teams-1-day", org_scope, day, "user_teams")
    if teams:
        slugs = sorted({t.get("slug", "?") for t in teams})
        result.add("Team mapping", "PASS", f"{len(teams)} membership rows, teams: {slugs[:10]}")
    else:
        result.add(
            "Team mapping",
            "WARN",
            "No team rows. GitHub omits teams with fewer than 5 seated users.",
        )

    if args.enterprise:
        ent_scope = f"/enterprises/{args.enterprise}"
        check_metrics_report(result, token, "users-1-day", ent_scope, day, "user_metrics")

    logins = check_seats(result, token, args.org, args.show_values)
    target = args.user or (logins[0] if logins else None)
    if target:
        check_billing(result, token, args.org, target, day, args.show_values)
    else:
        result.add("Billing: premium_request usage (per user)", "SKIP", "No seat login available.")

    if args.as_json:
        print(json.dumps([{"check": n, "status": s, "detail": d} for n, s, d in result.checks], indent=2))
    else:
        result.render(colour=sys.stdout.isatty())

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
