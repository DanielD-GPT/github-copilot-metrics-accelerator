"""Cross-instance ingestion lock backed by a renewable Azure Blob lease."""
from __future__ import annotations

import logging
import threading
import time

from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobLeaseClient, BlobServiceClient

from .config import Settings

LOGGER = logging.getLogger(__name__)
LEASE_SECONDS = 60
RENEW_INTERVAL_SECONDS = 30
RENEW_RETRY_SECONDS = 5
RENEW_FAILURE_DEADLINE_SECONDS = 25
LOCK_BLOB_NAME = ".locks/copilot-metrics-ingestion.lock"


class RunInProgress(RuntimeError):
    """Another Function instance currently owns the ingestion lease."""


class IngestionLease:
    def __init__(
        self,
        settings: Settings,
        credential: DefaultAzureCredential | None = None,
    ):
        service = BlobServiceClient(
            account_url=f"https://{settings.lake_account_name}.blob.core.windows.net",
            credential=credential or DefaultAzureCredential(),
        )
        self._blob = service.get_blob_client(
            container=settings.lake_filesystem_name,
            blob=LOCK_BLOB_NAME,
        )
        self._lease: BlobLeaseClient | None = None
        self._stop = threading.Event()
        self._renewal_thread: threading.Thread | None = None
        self._renewal_error: Exception | None = None

    def __enter__(self) -> IngestionLease:
        try:
            self._blob.upload_blob(b"", overwrite=False)
        except ResourceExistsError:
            pass

        try:
            self._lease = self._blob.acquire_lease(lease_duration=LEASE_SECONDS)
        except HttpResponseError as exc:
            if exc.status_code == 409:
                raise RunInProgress("An ingestion run is already in progress.") from exc
            raise

        self._renewal_thread = threading.Thread(
            target=self._renew,
            name="ingestion-lease-renewal",
            daemon=True,
        )
        self._renewal_thread.start()
        return self

    def _renew(self) -> None:
        while not self._stop.wait(RENEW_INTERVAL_SECONDS):
            deadline = time.monotonic() + RENEW_FAILURE_DEADLINE_SECONDS
            while self._lease is not None:
                try:
                    self._lease.renew()
                    break
                except Exception as exc:
                    if time.monotonic() >= deadline:
                        self._renewal_error = exc
                        LOGGER.exception("Could not renew the ingestion blob lease.")
                        return
                    LOGGER.warning("Blob lease renewal failed; retrying.", exc_info=True)
                    if self._stop.wait(RENEW_RETRY_SECONDS):
                        return

    def ensure_healthy(self) -> None:
        if self._renewal_error is not None:
            raise RuntimeError("The ingestion lock could not be renewed.") from self._renewal_error

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._renewal_thread is not None:
            self._renewal_thread.join(timeout=RENEW_INTERVAL_SECONDS + 5)
        if self._lease is not None:
            try:
                self._lease.release()
            except HttpResponseError:
                LOGGER.exception("Could not release the ingestion blob lease.")
