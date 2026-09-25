"""Tests for the Fabric ingestion blob lease."""
from threading import Event

import pytest
from azure.core.exceptions import HttpResponseError, ResourceExistsError

from shared.ingestion_lock import IngestionLease, RunInProgress


class _FakeLease:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


class _FakeBlob:
    def __init__(self, lease=None, acquire_error=None):
        self.lease = lease or _FakeLease()
        self.acquire_error = acquire_error

    def upload_blob(self, _data, overwrite):
        assert overwrite is False
        raise ResourceExistsError("already exists")

    def acquire_lease(self, lease_duration):
        assert lease_duration == 60
        if self.acquire_error:
            raise self.acquire_error
        return self.lease


def _subject(blob):
    subject = object.__new__(IngestionLease)
    subject._blob = blob
    subject._lease = None
    subject._stop = Event()
    subject._renewal_thread = None
    subject._renewal_error = None
    return subject


def test_existing_lock_blob_can_be_leased_and_released():
    lease = _FakeLease()
    subject = _subject(_FakeBlob(lease=lease))

    with subject:
        subject.ensure_healthy()

    assert lease.released is True


def test_lease_conflict_reports_run_in_progress():
    conflict = HttpResponseError("leased")
    conflict.status_code = 409

    with pytest.raises(RunInProgress):
        _subject(_FakeBlob(acquire_error=conflict)).__enter__()


def test_renewal_failure_is_surfaced():
    subject = _subject(_FakeBlob())
    subject._renewal_error = RuntimeError("renew failed")

    with pytest.raises(RuntimeError, match="could not be renewed"):
        subject.ensure_healthy()
