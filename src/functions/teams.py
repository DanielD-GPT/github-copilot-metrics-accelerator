"""Teams bot endpoint, implemented as a Teams Outgoing Webhook.

Chosen over a full Bot Framework bot because it needs no Azure Bot Service resource
and no separate app registration — Teams signs each request with a shared secret.

Commands (after the @mention):
    jimmy              -> spend breakdown for that user
    me                 -> spend for the caller
    top [n]            -> highest spenders
    team <name>        -> spend for a cost center / team
    summary            -> enterprise totals
    help
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re

import azure.functions as func

from shared.config import load_settings
from shared.queries import SpendQueries
from shared.secrets import get_secret

bp = func.Blueprint()
LOGGER = logging.getLogger(__name__)

SECRET_NAME = os.environ.get("TEAMS_WEBHOOK_SECRET_NAME", "teams-webhook-secret")
MENTION_TAG = re.compile(r"<at>.*?</at>", re.IGNORECASE)


def _verify_signature(raw_body: bytes, auth_header: str, shared_secret: str) -> bool:
    """Teams signs the raw body with HMAC-SHA256 using the base64-decoded secret."""
    if not _looks_like_hmac_header(auth_header):
        return False

    try:
        key = base64.b64decode(shared_secret, validate=True)
    except (binascii.Error, ValueError):
        LOGGER.error("Teams webhook secret is not valid base64.")
        return False

    expected = base64.b64encode(hmac.new(key, raw_body, hashlib.sha256).digest()).decode()
    provided = auth_header[len("HMAC "):].strip()

    # Constant-time comparison: never use == on a signature.
    return hmac.compare_digest(expected, provided)


def _looks_like_hmac_header(auth_header: str) -> bool:
    """Cheap shape check so unauthenticated traffic never reaches Key Vault."""
    if not auth_header or not auth_header.startswith("HMAC "):
        return False
    return bool(auth_header[len("HMAC "):].strip())


def _card(title: str, facts: list[tuple[str, str]], note: str | None = None) -> dict:
    body: list[dict] = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}
    ]
    if facts:
        body.append(
            {
                "type": "FactSet",
                "facts": [{"title": k, "value": v} for k, v in facts],
            }
        )
    if note:
        body.append(
            {
                "type": "TextBlock",
                "text": note,
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "spacing": "Medium",
            }
        )

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }


def _text(message: str) -> dict:
    return {"type": "message", "text": message}


def _money(value) -> str:
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


HELP = (
    "**Copilot spend bot**\n\n"
    "- `@bot jimmy` — spend breakdown for a user\n"
    "- `@bot me` — your own spend\n"
    "- `@bot top 10` — highest spenders\n"
    "- `@bot team Platform` — spend for a team or cost center\n"
    "- `@bot summary` — enterprise totals"
)


def _handle(command: str, caller_login: str, queries: SpendQueries) -> dict:
    parts = command.split()
    if not parts:
        return _text(HELP)

    verb = parts[0].lower()
    since, until = SpendQueries.resolve_window(None, None)
    window = f"{since.isoformat()} to {until.isoformat()}"

    if verb in ("help", "?"):
        return _text(HELP)

    if verb == "summary":
        data = queries.summary(since, until)
        return _card(
            f"Copilot spend — {window}",
            [
                ("Total spend", _money(data.get("total_spend"))),
                ("Active users", str(data.get("active_users", 0))),
                ("Models used", str(data.get("models_used", 0))),
                ("Premium requests", f"{float(data.get('total_requests') or 0):,.0f}"),
            ],
        )

    if verb == "top":
        limit = SpendQueries.clamp_limit(int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10)
        rows = queries.top_spenders(since, until, limit)
        if not rows:
            return _text(f"No spend recorded for {window}.")
        return _card(
            f"Top {len(rows)} spenders — {window}",
            [(r["user_login"], _money(r["spend"])) for r in rows],
        )

    if verb == "team":
        if len(parts) < 2:
            return _text("Usage: `@bot team <name>`")
        team = " ".join(parts[1:])
        rows = queries.team_breakdown(team, since, until)
        if not rows:
            return _text(f"No spend found for team **{team}** in {window}.")
        total = sum(r.get("spend") or 0 for r in rows)
        facts = [("Total", _money(total))]
        facts += [(f"{r['user_login']} · {r['model_name']}", _money(r["spend"])) for r in rows[:15]]
        return _card(f"{team} — {window}", facts)

    login = caller_login if verb == "me" else parts[0]
    if not login:
        return _text("Could not determine your GitHub login. Try `@bot <your-github-login>`.")

    exact = queries.user_exact_total(login, since, until)
    if not exact or float(exact.get("net_amount") or 0) == 0:
        return _text(f"No spend recorded for **{login}** in {window}.")

    breakdown = queries.user_breakdown(login, since, until)
    facts = [("Total (exact)", _money(exact.get("net_amount")))]
    facts += [
        (f"{r['model_name']} · {r['editor_family']} · {r['feature']}", _money(r["spend"]))
        for r in breakdown[:12]
    ]

    return _card(
        f"{login} — {window}",
        facts,
        note=(
            "Total is exact. The editor/feature split is a proportional allocation "
            "from aggregate engagement data, not an audit record."
        ),
    )


@bp.function_name(name="teams_bot")
@bp.route(route="teams", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def teams_bot(req: func.HttpRequest) -> func.HttpResponse:
    """Anonymous at the platform level; authenticated by Teams' HMAC signature."""
    settings = load_settings()
    raw_body = req.get_body()
    auth_header = req.headers.get("Authorization", "")

    # Reject obviously unsigned traffic before spending a Key Vault call on it.
    if not _looks_like_hmac_header(auth_header):
        return func.HttpResponse("Unauthorized", status_code=401)

    try:
        shared_secret = get_secret(settings, SECRET_NAME)
    except Exception:
        LOGGER.exception("Could not read the Teams webhook secret.")
        return func.HttpResponse("Bot not configured.", status_code=503)

    if not shared_secret or not _verify_signature(raw_body, auth_header, shared_secret):
        LOGGER.warning("Rejected Teams webhook call with an invalid signature.")
        return func.HttpResponse("Unauthorized", status_code=401)

    try:
        activity = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return func.HttpResponse(
            json.dumps(_text("Could not parse that request.")),
            status_code=200,
            mimetype="application/json",
        )

    command = MENTION_TAG.sub("", activity.get("text") or "").strip()
    caller = (activity.get("from") or {}).get("name") or ""

    try:
        response = _handle(command, caller, SpendQueries(settings))
    except Exception:
        LOGGER.exception("Teams command failed: %s", command)
        response = _text("Something went wrong running that query.")

    return func.HttpResponse(json.dumps(response), status_code=200, mimetype="application/json")
