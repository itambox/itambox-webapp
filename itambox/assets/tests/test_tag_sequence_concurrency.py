"""Regression test: AssetTagSequence.next_tag() must be safe under concurrency.

Before the select_for_update fix, two concurrent saves could format the same
tag from a stale read and collide on the asset_tag unique constraint.
"""

import threading
import time
from types import SimpleNamespace

import pytest
from django.db import connection, transaction
from django.utils import timezone

from assets.models import AssetTagSequence
from organization.models import Tenant


@pytest.mark.django_db(transaction=True)
@pytest.mark.serial_only
def test_next_tag_is_unique_under_concurrent_claims():
    seq = AssetTagSequence.all_objects.create(prefix="CONC-", next_value=1, zero_padding=4)

    claimed = []
    claimed_lock = threading.Lock()
    barrier = threading.Barrier(4)

    def claim(n):
        try:
            barrier.wait(timeout=10)
            local = AssetTagSequence.all_objects.get(pk=seq.pk)
            for _ in range(n):
                tag = local.next_tag()
                with claimed_lock:
                    claimed.append(tag)
        finally:
            connection.close()

    threads = [threading.Thread(target=claim, args=(5,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(claimed) == 20
    assert len(set(claimed)) == 20, f"Duplicate tags claimed: {sorted(claimed)}"

    seq.refresh_from_db()
    assert seq.next_value == 21


def _archive_sequence_worker(sequence_id, archive_ready, release_archive, backend_pids, errors):
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_pids.append(cursor.fetchone()[0])
            locked = AssetTagSequence.all_objects.select_for_update().get(pk=sequence_id)
            locked.deleted_at = timezone.now()
            locked.save(update_fields=["deleted_at"])
            archive_ready.set()
            if not release_archive.wait(timeout=30):
                raise AssertionError("archive transaction was not released")
    except Exception as exc:
        errors.append(exc)
        archive_ready.set()
    finally:
        connection.close()


def _allocate_tag_worker(asset, allocation_gate, allocation_started, allocation_done, backend_pids, tags, errors):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            backend_pids.append(cursor.fetchone()[0])
        allocation_started.set()
        allocation_gate.wait()
        tags.append(AssetTagSequence.get_next_tag_for_asset(asset))
    except Exception as exc:
        errors.append(exc)
    finally:
        allocation_done.set()
        connection.close()


def _wait_for_archive_block(waiter_pid, blocker_pid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT wait_event_type, pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid = %s",
                [waiter_pid],
            )
            state = cursor.fetchone()
        if state and state[0] == "Lock" and blocker_pid in state[1]:
            return True
        time.sleep(0.01)
    return False


@pytest.mark.django_db(transaction=True)
@pytest.mark.serial_only
def test_allocation_waiting_for_archive_uses_replacement_sequence():
    tenant = Tenant.objects.create(name="Concurrent Archive Tenant", slug="concurrent-archive")
    archived = AssetTagSequence.all_objects.create(tenant=tenant, prefix="OLD-", zero_padding=4)
    replacement = AssetTagSequence.all_objects.create(tenant=tenant, prefix="NEW-", zero_padding=4)
    asset = SimpleNamespace(tenant_id=tenant.pk, category=None)

    archive_ready = threading.Event()
    release_archive = threading.Event()
    allocation_gate = threading.Event()
    allocation_started = threading.Event()
    allocation_done = threading.Event()
    archive_pid = []
    allocation_pid = []
    allocated_tags = []
    errors = []

    archiver = threading.Thread(
        target=_archive_sequence_worker,
        args=(archived.pk, archive_ready, release_archive, archive_pid, errors),
    )
    allocator = threading.Thread(
        target=_allocate_tag_worker,
        args=(asset, allocation_gate, allocation_started, allocation_done, allocation_pid, allocated_tags, errors),
    )
    archiver.start()
    allocator.start()
    try:
        assert archive_ready.wait(timeout=10), "archive transaction did not acquire its row lock"
        assert allocation_started.wait(timeout=10), "allocation thread did not start"
        assert not errors, errors
        allocation_gate.set()
        assert _wait_for_archive_block(allocation_pid[0], archive_pid[0]), (
            "allocation did not wait on the archive transaction's row lock"
        )
    finally:
        release_archive.set()
        allocation_gate.set()
        archiver.join(timeout=30)
        allocator.join(timeout=30)

    assert not archiver.is_alive(), "archive thread did not terminate"
    assert not allocator.is_alive(), "allocation thread did not terminate"
    assert not errors, errors
    assert allocation_done.is_set()
    assert allocated_tags == ["NEW-0001"]
    archived.refresh_from_db()
    replacement.refresh_from_db()
    assert archived.next_value == 1
    assert replacement.next_value == 2
