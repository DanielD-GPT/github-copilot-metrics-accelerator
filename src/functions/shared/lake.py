"""Raw zone writer.

Every payload is archived verbatim before parsing, so a transform bug can be fixed
and the warehouse rebuilt without re-paying the per-user billing request cost.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient

from .config import Settings

LOGGER = logging.getLogger(__name__)

_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")


def _safe_segment(value: str) -> str:
    """Keep org and dataset names from escaping their partition directory."""
    return _UNSAFE_SEGMENT.sub("_", value).strip(".") or "unnamed"


def _json_default(value: Any) -> str:
    """Serialize Decimal as a string, never a float, so money keeps full precision."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON.")


class RawZone:
    def __init__(self, settings: Settings, credential: DefaultAzureCredential | None = None):
        self._settings = settings
        self._service = DataLakeServiceClient(
            account_url=settings.lake_url,
            credential=credential or DefaultAzureCredential(),
        )
        self._filesystem = self._service.get_file_system_client(settings.lake_filesystem_name)
        try:
            self._filesystem.create_file_system()
        except ResourceExistsError:
            pass

    def write(self, dataset: str, partition_date: date, payload: Any, suffix: str = "") -> str:
        dataset = _safe_segment(dataset)
        suffix = _safe_segment(suffix) if suffix else ""

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        name = f"{dataset}{('_' + suffix) if suffix else ''}_{stamp}.json"
        path = f"{dataset}/dt={partition_date.isoformat()}/{name}"

        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
        file_client = self._filesystem.get_file_client(path)
        file_client.upload_data(body, overwrite=True)

        LOGGER.info("Archived %s bytes to %s", len(body), path)
        return path
