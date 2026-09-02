"""Cached Key Vault secret access."""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from .config import Settings

_CACHE: dict[str, tuple[str, datetime]] = {}
_LOCK = threading.Lock()
_TTL = timedelta(minutes=30)


def get_secret(settings: Settings, name: str) -> str:
    """Read a secret, caching briefly so rotation is still picked up within the hour."""
    now = datetime.now(UTC)

    with _LOCK:
        cached = _CACHE.get(name)
        if cached and cached[1] > now:
            return cached[0]

    client = SecretClient(vault_url=settings.key_vault_uri, credential=DefaultAzureCredential())
    value = client.get_secret(name).value or ""

    with _LOCK:
        _CACHE[name] = (value, now + _TTL)

    return value
