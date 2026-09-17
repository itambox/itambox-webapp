"""#496: real PostgreSQL arbitration for disposal ownership.

Two independent connections, observed locks (`pg_blocking_pids` / `pg_stat_activity`)
and bounded monotonic deadlines; every thread releases its lock and closes its
connection in ``finally``. Child threads get an explicit scope/actor through
``TaskContext`` instead of inheriting the parent's context.

The raw-INSERT competition below proves the database constraint; the service-level
tests exercise the public disposal/cancellation services and their ordering.
"""

import contextlib
import datetime
import queue
import threading
import time
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from assets.models import Asset, AssetDisposal, DisposalMethodChoices, StatusLabel
from assets.services import cancel_asset_disposal, dispose_asset
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin

pytestmark = [pytest.mark.serial_only]


def _start(target):
    """Run ``target`` on its own connection and report that backend's PID."""
    arrived = queue.Queue()
    results, errors = [], []

    def worker():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                arrived.put(cursor.fetchone()[0])
            results.append(target())
        except Exception as error:  # noqa: BLE001 - reported to the caller
            errors.append(error)
        finally:
            connections["default"].close()

    thread = threading.Thread(target=worker)
    thread.start()
    return thread, arrived.get(timeout=15), results, errors


def _observe_lock_wait(pid, blocker_pid):
    """Require PostgreSQL to name ``blocker_pid`` as the lock holder for ``pid``."""
    deadline = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT wait_event_type, wait_event, %s = ANY(pg_blocking_pids(pid)), "
                "(SELECT count(*) FROM pg_locks WHERE pid = %s AND NOT granted) "
                "FROM pg_stat_activity WHERE pid = %s",
                [blocker_pid, pid, pid],
            )
            last = cursor.fetchone()
            if last and last[0] == "Lock" and last[2] and last[3]:
                print("OBSERVED_DISPOSAL_LOCK_WAIT", pid, "blocked_by", blocker_pid, last)
                return
        threading.Event().wait(0.01)
    pytest.fail(f"backend {pid} never blocked on the {blocker_pid} transaction: {last}")


@contextlib.contextmanager
def _start_workers(release, *targets):
    """Start every worker, then always release the lock and join the threads.

    Worker startup itself is inside the protected block: if the second worker
    cannot even report its backend, the first one is still released and joined
    instead of leaking an open transaction into the test teardown.
    """
    started = []
    try:
        for target in targets:
            started.append(_start(target))
        yield started
    finally:
        release.set()
        for entry in started:
            entry[0].join(20)


def _finish(started):
    thread, pid, results, errors = started
    thread.join(20)
    assert not thread.is_alive(), "worker did not terminate after the lock was released"
    return pid, results, errors


class DisposalConcurrencyTests(TenantTestMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql")
        # The mixin may already have provisioned the tenant scope in super().setUp();
        # never create a second "Test Tenant" row for the same test.
        if getattr(self, "tenant", None) is None:
            self.setup_tenant_context()
        self.set_active_tenant(self.tenant)
        suffix = uuid.uuid4().hex[:8]
        self.deployable = StatusLabel.objects.create(name=f"Deployable {suffix}", type="deployable")
        StatusLabel.objects.create(type="archived", name=f"Archived {suffix}")
        StatusLabel.objects.create(type="pending", name=f"Pending {suffix}")
        self.asset = Asset.objects.create(
            asset_tag=f"CONC-496-{suffix}", name="Concurrency laptop", status=self.deployable, tenant=self.tenant
        )

    def _active_count(self):
        return AssetDisposal.all_objects.filter(asset_id=self.asset.pk, cancelled_at__isnull=True).count()

    def test_constraint_arbitrates_a_competing_active_disposal_under_an_observed_lock(self):
        """Raw INSERT competition: constraint proof, with the loser's wait observed."""
        inserted = threading.Event()
        released = threading.Event()

        def holder():
            with transaction.atomic():
                AssetDisposal.objects.create(
                    asset_id=self.asset.pk,
                    disposal_method=DisposalMethodChoices.RECYCLE,
                    disposal_date=datetime.date(2026, 6, 20),
                )
                inserted.set()
                assert released.wait(timeout=20), "holder was never released"
            return "committed"

        def challenger():
            assert inserted.wait(timeout=20), "holder never inserted its row"
            try:
                with transaction.atomic():
                    AssetDisposal.objects.create(
                        asset_id=self.asset.pk,
                        disposal_method=DisposalMethodChoices.RECYCLE,
                        disposal_date=datetime.date(2026, 6, 21),
                    )
            except IntegrityError as error:
                return error
            return "created"

        with _start_workers(released, holder, challenger) as (holder_started, challenger_started):
            _observe_lock_wait(challenger_started[1], holder_started[1])
            released.set()
        holder_pid, holder_results, holder_errors = _finish(holder_started)
        challenger_pid, challenger_results, challenger_errors = _finish(challenger_started)

        assert not holder_errors, holder_errors
        assert not challenger_errors, challenger_errors
        self.assertEqual(holder_results, ["committed"])
        self.assertIsInstance(challenger_results[0], IntegrityError)
        self.assertIn("uniq_active_disposal_per_asset", str(challenger_results[0]))
        self.assertEqual(self._active_count(), 1)

    def test_competing_dispose_service_calls_serialize_to_one_record(self):
        """Service-level arbitration: one winner, one clean rejection, one active record."""
        first_done = threading.Event()
        released = threading.Event()
        outcome = {}

        def first():
            with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk, operation="issue496_race"):
                with transaction.atomic():
                    outcome["first"] = dispose_asset(
                        asset=Asset._base_manager.get(pk=self.asset.pk),
                        disposal_method=DisposalMethodChoices.RECYCLE,
                        disposal_date=datetime.date(2026, 6, 22),
                        user=self.tenant_user,
                    )
                    first_done.set()
                    assert released.wait(timeout=20), "first disposal was never released"
            return "disposed"

        def second():
            assert first_done.wait(timeout=20), "first disposal never completed"
            try:
                with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk, operation="issue496_race"):
                    dispose_asset(
                        asset=Asset._base_manager.get(pk=self.asset.pk),
                        disposal_method=DisposalMethodChoices.RECYCLE,
                        disposal_date=datetime.date(2026, 6, 23),
                        user=self.tenant_user,
                    )
            except ValidationError as error:
                return error
            return "disposed"

        with _start_workers(released, first, second) as (first_started, second_started):
            _observe_lock_wait(second_started[1], first_started[1])
            released.set()
        _, first_results, first_errors = _finish(first_started)
        _, second_results, second_errors = _finish(second_started)

        assert not first_errors, first_errors
        assert not second_errors, second_errors
        self.assertEqual(first_results, ["disposed"])
        self.assertIsInstance(second_results[0], ValidationError)
        self.assertEqual(self._active_count(), 1)
        self.assertEqual(AssetDisposal.all_objects.filter(asset_id=self.asset.pk).count(), 1)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")

    def test_dispose_waits_for_cancellation_and_preserves_the_original_record(self):
        """A committed record is cancelled while a new disposal waits for its asset lock."""
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk, operation="issue496_race"):
            original = dispose_asset(
                asset=self.asset,
                disposal_method=DisposalMethodChoices.RECYCLE,
                disposal_date=datetime.date(2026, 6, 24),
                user=self.tenant_user,
            )
        cancelled = threading.Event()
        released = threading.Event()

        def canceller():
            with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk, operation="issue496_race"):
                with transaction.atomic():
                    cancel_asset_disposal(original, user=self.tenant_user, reason="recorded in error")
                    cancelled.set()
                    assert released.wait(timeout=20), "cancellation was never released"
            return "cancelled"

        def disposer():
            assert cancelled.wait(timeout=20), "cancellation never completed"
            with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk, operation="issue496_race"):
                return dispose_asset(
                    asset=Asset._base_manager.get(pk=self.asset.pk),
                    disposal_method=DisposalMethodChoices.RECYCLE,
                    disposal_date=datetime.date(2026, 6, 25),
                    user=self.tenant_user,
                ).pk

        with _start_workers(released, canceller, disposer) as (first_started, second_started):
            _observe_lock_wait(second_started[1], first_started[1])
            released.set()
        _, first_results, first_errors = _finish(first_started)
        _, second_results, second_errors = _finish(second_started)
        self.assertEqual(first_errors, [])
        self.assertEqual(second_errors, [])
        self.assertEqual(first_results, ["cancelled"])
        self.assertEqual(len(second_results), 1)
        self.assertNotEqual(second_results[0], original.pk)
        original.refresh_from_db()
        self.assertIsNotNone(original.cancelled_at)
        self.assertEqual(original.cancelled_by_id, self.tenant_user.pk)
        self.assertEqual(original.cancellation_reason, "recorded in error")
        self.assertEqual(original.disposal_date, datetime.date(2026, 6, 24))
        self.assertEqual(self._active_count(), 1)
        self.assertEqual(AssetDisposal.all_objects.filter(asset_id=self.asset.pk).count(), 2)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")
