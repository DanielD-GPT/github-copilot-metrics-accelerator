"""Security tests for the Teams webhook signature check.

The /api/teams route is anonymous at the platform level, so this HMAC check is the
only thing standing between the internet and per-employee spend data.
"""
import base64
import hashlib
import hmac

import pytest

pytest.importorskip("azure.functions")
pytest.importorskip("azure.keyvault.secrets")

from teams import MENTION_TAG, _looks_like_hmac_header, _money, _verify_signature  # noqa: E402

SECRET = base64.b64encode(b"a-shared-secret-from-teams").decode()


def sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(base64.b64decode(secret), body, hashlib.sha256).digest()
    return "HMAC " + base64.b64encode(digest).decode()


class TestVerifySignature:
    def test_accepts_a_correct_signature(self):
        body = b'{"text":"summary"}'
        assert _verify_signature(body, sign(body), SECRET) is True

    def test_rejects_a_tampered_body(self):
        header = sign(b'{"text":"summary"}')
        assert _verify_signature(b'{"text":"top 100"}', header, SECRET) is False

    def test_rejects_a_different_secret(self):
        body = b'{"text":"summary"}'
        other = base64.b64encode(b"attacker-secret").decode()
        assert _verify_signature(body, sign(body, other), SECRET) is False

    def test_rejects_missing_header(self):
        assert _verify_signature(b"{}", "", SECRET) is False

    def test_rejects_wrong_scheme(self):
        body = b"{}"
        raw = sign(body).removeprefix("HMAC ")
        assert _verify_signature(body, f"Bearer {raw}", SECRET) is False
        assert _verify_signature(body, raw, SECRET) is False

    def test_rejects_garbage_signature(self):
        assert _verify_signature(b"{}", "HMAC not-base64!!", SECRET) is False

    def test_rejects_non_base64_secret(self):
        assert _verify_signature(b"{}", sign(b"{}"), "not valid base64 %%%") is False

    def test_empty_body_still_verifies_consistently(self):
        assert _verify_signature(b"", sign(b""), SECRET) is True
        assert _verify_signature(b"x", sign(b""), SECRET) is False


class TestMentionStripping:
    def test_removes_the_bot_mention(self):
        assert MENTION_TAG.sub("", "<at>Copilot Spend</at> summary").strip() == "summary"

    def test_removes_multiple_mentions(self):
        text = "<at>Bot</at> team <at>Other</at> Platform"
        assert MENTION_TAG.sub("", text).split() == ["team", "Platform"]


class TestHeaderPreCheck:
    """Cheap shape check so unsigned traffic never reaches Key Vault."""

    def test_accepts_a_well_formed_header(self):
        assert _looks_like_hmac_header("HMAC abc123") is True

    def test_rejects_missing_or_empty(self):
        assert _looks_like_hmac_header("") is False
        assert _looks_like_hmac_header("HMAC ") is False
        assert _looks_like_hmac_header("HMAC") is False

    def test_rejects_other_schemes(self):
        assert _looks_like_hmac_header("Bearer abc123") is False


class TestMoneyFormatting:
    def test_formats_numbers(self):
        assert _money(1234.5) == "$1,234.50"
        assert _money(None) == "$0.00"

    def test_survives_unexpected_types(self):
        assert _money("not a number") == "$0.00"
        assert _money(object()) == "$0.00"
