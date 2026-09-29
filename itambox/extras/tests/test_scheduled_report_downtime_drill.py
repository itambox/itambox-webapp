"""Scheduled-report worker downtime / recovery drills for the Stable contract.

These exercises drive the *real* django-q scheduling path (``scheduler()``
against a recording broker) and the real task entry point instead of
re-implementing either, so the drill states exactly what a deployment sees:

* **Downtime replay** — while the worker is down the scheduler keeps firing
  registered schedules; after three missed hourly occurrences, each further
  scheduling pass replays the OLDEST missed occurrence (one per pass,
  ``catch_up`` behavior), never more.
* **Restart determinism** — once the schedule has caught up, later passes
  enqueue nothing until the next occurrence is due.
* **Redelivery idempotency** — re-running a delivery for the same intended
  occurrence (broker redelivery after the retry interval, duplicate queue
  entries) is a recorded no-op: no second generation, no second delivery.
  Idempotency is per occurrence, so an out-of-order replay (a newer
  occurrence accepted before an older one) never discards the older one.
* **Suppressed schedules stay suppressed** — an inactive schedule whose
  registration row leaked (delete interrupted) is skipped with a task result,
  even if the row is due; nothing is delivered.
* **Recovery** — a failed delivery leaves a per-target ledger and is recovered
  through ``Retry delivery``, which re-contacts only the failed targets with
  the recorded original recipients and payloads, re-validates the archived
  generation scope first, refuses inactive schedules, and claims the attempt
  atomically so two parallel requests deliver exactly once.
"""

from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.utils.module_loading import import_string
from django_q.conf import Conf
from django_q.models import Schedule
from django_q.scheduler import scheduler
from django_q.signing import SignedPackage

from extras.models import NotificationChannel, ReportGenerationArchive, ReportTemplate, ScheduledReport
from extras.tasks.reports import retry_failed_deliveries
from extras.views import handle_report_scheduling

TASK_PATH = "extras.tasks.reports.generate_scheduled_report_task"


@contextmanager
def queue_mode():
    """Dispatch through the queue instead of the suite's forced sync mode.

    The test suite pins ``Q_CLUSTER['sync']`` so most tests can execute tasks
    inline; the drill restores production dispatch semantics (the scheduler
    queues a signed package, a worker pulls it later) so replay, restart, and
    redelivery behavior is exercised as deployed.
    """
    with mock.patch.object(Conf, "SYNC", False):
        yield


class RecordingBroker:
    """In-memory cluster broker: records the signed task packages it receives."""

    list_key = "recording"

    def __init__(self):
        self.tasks = []

    def enqueue(self, pack):
        self.tasks.append(pack)
        return f"recording-{len(self.tasks)}"


def decode_task(pack):
    """Decode a signed task package into the payload the worker would run."""
    return SignedPackage.loads(pack)


def run_occurrence(task, **overrides):
    """Execute one queued occurrence exactly like a worker would."""
    kwargs = dict(task["kwargs"])
    kwargs.pop("q_options", None)
    kwargs.update(overrides)
    func = import_string(task["func"])
    return func(*task["args"], **kwargs)


class ScheduledReportDowntimeDrillTests(TransactionTestCase):
    """Worker downtime, replay, restart, and redelivery behavior.

    Runs on real transactions: the scheduler closes stale connections between
    passes and the fire-claim relies on committed conditional updates, so the
    drill must not sit inside the test runner's wrapping transaction.
    """

    def setUp(self):
        from organization.models import Tenant

        # Only the drill's own schedule may be due; the deployment-registered
        # system schedules are pushed out of the firing window so every
        # scheduler pass and every broker assertion is about the drill.
        Schedule.objects.exclude(func=TASK_PATH).update(next_run=timezone.now() + timedelta(days=400))

        self.tenant = Tenant.objects.create(name="Drill Tenant", slug="drill-tenant")
        self.template = ReportTemplate.objects.create(
            name="Drill Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )

    def _hourly_schedule(self, *, save_to_archive=True):
        sched = ScheduledReport.objects.create(
            name="Hourly Drill Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_HOURLY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="drill@example.com",
            save_to_archive=save_to_archive,
            is_active=True,
        )
        handle_report_scheduling(sched)
        sched.refresh_from_db()
        return sched

    def test_missed_hourly_occurrences_replay_one_per_pass_after_downtime(self):
        broker = RecordingBroker()
        sched = self._hourly_schedule()

        # The worker is down while ~3 hourly occurrences come due.
        anchor = timezone.now() - timedelta(hours=2, minutes=55)
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=anchor)

        # Each scheduling pass publishes exactly one replay of the oldest missed
        # occurrence; the row advances one interval per pass.
        with queue_mode():
            scheduler(broker=broker)
        self.assertEqual(len(broker.tasks), 1)
        with queue_mode():
            scheduler(broker=broker)
        self.assertEqual(len(broker.tasks), 2)
        with queue_mode():
            scheduler(broker=broker)
        self.assertEqual(len(broker.tasks), 3)
        # Caught up: the next pass must not enqueue a fourth occurrence.
        with queue_mode():
            scheduler(broker=broker)
        self.assertEqual(len(broker.tasks), 3)

        occurrences = [decode_task(pack) for pack in broker.tasks]
        intended = [occurrence["kwargs"]["intended_fire_at"] for occurrence in occurrences]
        self.assertEqual(len(set(intended)), 3)
        self.assertEqual(intended, sorted(intended))
        self.assertEqual(occurrences[0]["kwargs"]["intended_fire_at"], anchor.isoformat())
        for occurrence in occurrences:
            self.assertEqual(occurrence["func"], TASK_PATH)
            self.assertEqual(int(occurrence["args"][0]), sched.pk)

    def test_restart_and_redelivery_never_deliver_an_occurrence_twice(self):
        sched = self._hourly_schedule()
        anchor = timezone.now() - timedelta(minutes=30)
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=anchor)

        broker = RecordingBroker()
        with queue_mode():
            scheduler(broker=broker)
        self.assertEqual(len(broker.tasks), 1)
        task = decode_task(broker.tasks[0])

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as deliver_email,
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=True),
        ):
            result = run_occurrence(task)
            self.assertTrue(result)
            self.assertEqual(deliver_email.call_count, 1)

            # Broker redelivery of the same occurrence (ack lost at the retry
            # interval): recorded no-op, nothing is generated or delivered again.
            redelivered = run_occurrence(task)
            self.assertEqual(redelivered.code, "report.fire_already_accepted")
            self.assertEqual(deliver_email.call_count, 1)

        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "success")
        self.assertEqual(
            ReportGenerationArchive.objects.filter(scheduled_report=sched).count(),
            1,
        )

    def test_out_of_order_replay_accepts_every_occurrence_exactly_once(self):
        """A newer occurrence must never discard an older, unaccepted one.

        Parallel workers or a drained catch-up backlog can deliver the newer
        occurrence first; per-occurrence idempotency (not a global high-water
        mark) is what keeps the older one from being dropped.
        """
        sched = self._hourly_schedule()
        anchor = timezone.now() - timedelta(minutes=30)
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=anchor)

        broker = RecordingBroker()
        with queue_mode():
            scheduler(broker=broker)
        self.assertEqual(len(broker.tasks), 1)
        task = decode_task(broker.tasks[0])

        newer_at = (timezone.now() - timedelta(minutes=10)).replace(microsecond=0)
        older_at = newer_at - timedelta(hours=1)

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as deliver_email,
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=True),
        ):
            # The newer occurrence wins the claim first, as a parallel worker
            # or a mis-ordered queue drain would produce.
            first = run_occurrence(task, intended_fire_at=newer_at.isoformat())
            self.assertTrue(first)
            # The older occurrence must still run: it was never accepted.
            second = run_occurrence(task, intended_fire_at=older_at.isoformat())
            self.assertNotEqual(second.code, "report.fire_already_accepted")
            self.assertTrue(second)
            self.assertEqual(deliver_email.call_count, 2)

            # Each occurrence is still individually idempotent: an exact
            # redelivery of the older occurrence is a no-op, and the marker
            # stays at the newest accepted time.
            redelivered = run_occurrence(task, intended_fire_at=older_at.isoformat())
            self.assertEqual(redelivered.code, "report.fire_already_accepted")
            self.assertEqual(deliver_email.call_count, 2)

        sched.refresh_from_db()
        self.assertEqual(sched.last_accepted_fire_at, newer_at)
        self.assertEqual(ReportGenerationArchive.objects.filter(scheduled_report=sched).count(), 2)

    def test_a_failed_delivery_is_recovered_by_retry_without_duplicate_sends(self):
        sched = self._hourly_schedule()
        anchor = timezone.now() - timedelta(minutes=30)
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=anchor)

        broker = RecordingBroker()
        with queue_mode():
            scheduler(broker=broker)
        task = decode_task(broker.tasks[0])

        # The occurrence runs while the mail transport is broken.
        with mock.patch("extras.tasks.reports._deliver_report_email", side_effect=RuntimeError("smtp down")):
            failed = run_occurrence(task)
        self.assertEqual(failed.code, "report.delivery_failed")
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "failed")
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        self.assertEqual(archive.delivery_status, "failed")

        # Operator recovery: retry re-contacts only the failed target.
        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)
        self.assertEqual(outcome.code, "retry.completed")
        self.assertEqual(outcome.retried, 1)
        self.assertEqual(email_retry.call_count, 1)
        archive.refresh_from_db()
        self.assertEqual(archive.delivery_status, "success")
        email_target = next(t for t in archive.delivery_targets if t["target"] == "email")
        self.assertEqual(email_target["status"], "ok")
        self.assertTrue(email_target["retried"])
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "success")

    def test_inactive_schedule_stays_suppressed_even_with_a_leaked_registration(self):
        broker = RecordingBroker()
        sched = self._hourly_schedule()
        # Simulate a delete interrupted mid-way: the row stays registered while
        # the saved schedule is already deactivated.
        sched.is_active = False
        sched.save(update_fields=["is_active"])
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=timezone.now() - timedelta(minutes=5))

        with queue_mode():
            scheduler(broker=broker)
        self.assertEqual(len(broker.tasks), 1)  # the leaked row still publishes…

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as deliver_email:
            result = run_occurrence(decode_task(broker.tasks[0]))

        # …but the worker refuses it: no generation, no delivery, no resume.
        self.assertEqual(result.code, "report.inactive")
        deliver_email.assert_not_called()
        self.assertFalse(ReportGenerationArchive.objects.filter(scheduled_report=sched).exists())


class ScheduledReportDeliveryRecoveryTests(TestCase):
    """Retry delivery re-attempts exactly the failed targets."""

    def setUp(self):
        from organization.models import Tenant

        self.tenant = Tenant.objects.create(name="Recovery Tenant", slug="recovery-tenant")
        self.template = ReportTemplate.objects.create(
            name="Recovery Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )

    def _deliver_once(self, sched, *, email_effect, channel_result=True):
        if isinstance(email_effect, BaseException):
            email_patch = mock.patch("extras.tasks.reports._deliver_report_email", side_effect=email_effect)
        else:
            email_patch = mock.patch("extras.tasks.reports._deliver_report_email", return_value=email_effect)
        with (
            email_patch,
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=channel_result) as channel,
        ):
            from extras.tasks.reports import generate_scheduled_report_task

            result = generate_scheduled_report_task(sched.pk)
        return result, channel

    def test_retry_skips_successful_targets_and_repairs_the_ledger(self):
        channel = NotificationChannel.objects.create(
            name="Ops Email Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        sched = ScheduledReport.objects.create(
            name="Partial Recovery Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.channels.add(channel)

        # First run: email transport fails, the channel delivery succeeds.
        result, channel_mock = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_partial")
        self.assertEqual(channel_mock.call_count, 1)
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        self.assertEqual(archive.delivery_status, "partial")

        # Retry: only the failed (email) target is re-contacted.
        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.completed")
        self.assertEqual(outcome.retried, 1)
        self.assertEqual(email_retry.call_count, 1)
        self.assertEqual(channel_mock.call_count, 1)  # never duplicated

        archive.refresh_from_db()
        self.assertEqual(archive.delivery_status, "success")
        by_target = {t["target"]: t for t in archive.delivery_targets}
        self.assertEqual(by_target["email"]["status"], "ok")
        self.assertTrue(by_target["email"]["retried"])
        self.assertEqual(by_target[f"channel:{channel.pk}"]["status"], "ok")
        self.assertFalse(by_target[f"channel:{channel.pk}"]["retried"])

    def test_retry_without_a_retained_archive_reports_no_output(self):
        sched = ScheduledReport.objects.create(
            name="Archive-free Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=False,
            is_active=True,
        )
        self._deliver_once(sched, email_effect=RuntimeError("smtp down"))

        outcome = retry_failed_deliveries(sched)
        self.assertEqual(outcome.code, "retry.no_archive")

    def test_retry_without_recorded_failures_reports_nothing_to_do(self):
        sched = ScheduledReport.objects.create(
            name="Clean Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        self._deliver_once(sched, email_effect=True)

        outcome = retry_failed_deliveries(sched)
        self.assertEqual(outcome.code, "retry.no_recorded_failures")

    def test_retry_is_refused_once_cross_tenant_authorization_is_revoked(self):
        from django.contrib.auth import get_user_model

        from extras.models import ScheduledReportScopeAuthorization
        from organization.models import Tenant

        tenant_a = Tenant.objects.create(name="Scope A", slug="scope-a")
        tenant_b = Tenant.objects.create(name="Scope B", slug="scope-b")
        actor = get_user_model().objects.create_superuser(
            username="scope-approver", password="password123", email="approver@example.com"
        )
        sched = ScheduledReport.objects.create(
            name="Cross-tenant Schedule",
            report=self.template,
            tenant=None,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.filter_tenants.set([tenant_a, tenant_b])
        ScheduledReportScopeAuthorization.approve(sched, actor)

        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")

        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=sched)
        authorization.revoked_at = timezone.now()
        authorization.revoked_by = actor
        authorization.save(update_fields=["revoked_at", "revoked_by"])

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.scope_unauthorized")
        email_retry.assert_not_called()

    def test_retry_replays_the_recorded_recipients_and_payloads(self):
        channel = NotificationChannel.objects.create(
            name="Retry Replay Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "channel@example.com"},
        )
        sched = ScheduledReport.objects.create(
            name="Replay Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="original@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.channels.add(channel)

        # First run: email fails, the channel rejects the notification.
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"), channel_result=False)
        self.assertEqual(result.code, "report.delivery_failed")
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        recorded_channel = next(t for t in archive.delivery_targets if t["target"].startswith("channel:"))
        recorded_body = recorded_channel["details"]["payload"]["body"]

        # The operator edits the schedule afterwards; the retry must replay the
        # recorded original targets, not the since-edited configuration.
        sched.recipients = "edited@example.com"
        sched.save(update_fields=["recipients"])

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry,
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=True) as channel_retry,
        ):
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.completed")
        recipients = email_retry.call_args.args[3]
        self.assertEqual(recipients, ["original@example.com"])
        # The channel gets the recorded original notification (with its summary
        # cards), never a reduced reconstruction.
        retry_body = channel_retry.call_args.args[2]
        self.assertEqual(retry_body, recorded_body)
        self.assertNotIn("is being redelivered", retry_body)

    def test_concurrent_retries_deliver_exactly_once(self):
        sched = ScheduledReport.objects.create(
            name="Concurrent Retry Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")

        nested = {}

        def deliver_and_race(sched_arg, template, output, recipients):
            # While this attempt is in flight, a parallel request arrives with
            # the same failed ledger.
            nested["outcome"] = retry_failed_deliveries(sched)
            return True

        with mock.patch("extras.tasks.reports._deliver_report_email", side_effect=deliver_and_race) as email_retry:
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.completed")
        self.assertEqual(nested["outcome"].code, "retry.in_progress")
        # Only the claiming attempt contacted the failed target.
        self.assertEqual(email_retry.call_count, 1)
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        self.assertEqual(archive.delivery_status, "success")
        self.assertEqual(archive.retry_claim_token, "")
        self.assertIsNone(archive.retry_claim_expires_at)

    def test_an_interrupted_retry_claim_is_recoverable_after_the_lease(self):
        sched = ScheduledReport.objects.create(
            name="Interrupted Claim Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)

        # A crashed attempt left its claim behind: within the lease the retry
        # is refused, after the lease it is recoverable.
        ReportGenerationArchive.objects.filter(pk=archive.pk).update(
            retry_claim_token="interrupted-attempt",
            retry_claim_expires_at=timezone.now() + timedelta(minutes=14),
        )
        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            held = retry_failed_deliveries(sched)
        self.assertEqual(held.code, "retry.in_progress")
        email_retry.assert_not_called()

        ReportGenerationArchive.objects.filter(pk=archive.pk).update(
            retry_claim_expires_at=timezone.now() - timedelta(minutes=1),
        )
        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            recovered = retry_failed_deliveries(sched)
        self.assertEqual(recovered.code, "retry.completed")
        self.assertEqual(email_retry.call_count, 1)

    def test_retry_is_refused_while_the_schedule_is_inactive(self):
        sched = ScheduledReport.objects.create(
            name="Paused Retry Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")
        sched.refresh_from_db()
        self.assertTrue(sched.delivery_retryable)

        sched.is_active = False
        sched.save(update_fields=["is_active"])
        sched.refresh_from_db()

        # The action no longer surfaces and the recovery path refuses: a paused
        # schedule never dispatches externally.
        self.assertFalse(sched.delivery_retryable)
        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)
        self.assertEqual(outcome.code, "retry.inactive")
        email_retry.assert_not_called()

    def test_retry_is_refused_after_scope_reduction_despite_current_approval(self):
        from django.contrib.auth import get_user_model

        from extras.models import ScheduledReportScopeAuthorization
        from organization.models import Tenant

        tenant_a = Tenant.objects.create(name="Reduced Scope A", slug="reduced-scope-a")
        tenant_b = Tenant.objects.create(name="Reduced Scope B", slug="reduced-scope-b")
        actor = get_user_model().objects.create_superuser(
            username="reduction-approver", password="password123", email="reduction-approver@example.com"
        )
        sched = ScheduledReport.objects.create(
            name="Reduced Scope Schedule",
            report=self.template,
            tenant=None,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.filter_tenants.set([tenant_a, tenant_b])
        ScheduledReportScopeAuthorization.approve(sched, actor)

        # The archived run was generated under the approved A+B scope.
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")

        # The operator reduces the schedule to tenant A and re-approves only
        # the reduced scope: the retry must not legitimize the archived A+B
        # export through the new approval.
        sched.filter_tenants.set([tenant_a])
        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=sched)
        authorization.scope_tenant_ids = [tenant_a.pk]
        authorization.save(update_fields=["scope_tenant_ids"])

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)
        self.assertEqual(outcome.code, "retry.scope_unauthorized")
        email_retry.assert_not_called()

    def test_retry_still_delivers_while_the_original_approval_remains_valid(self):
        from django.contrib.auth import get_user_model

        from extras.models import ScheduledReportScopeAuthorization
        from organization.models import Tenant

        tenant_a = Tenant.objects.create(name="Standing Scope A", slug="standing-scope-a")
        tenant_b = Tenant.objects.create(name="Standing Scope B", slug="standing-scope-b")
        actor = get_user_model().objects.create_superuser(
            username="standing-approver", password="password123", email="standing-approver@example.com"
        )
        sched = ScheduledReport.objects.create(
            name="Standing Approval Schedule",
            report=self.template,
            tenant=None,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.filter_tenants.set([tenant_a, tenant_b])
        ScheduledReportScopeAuthorization.approve(sched, actor)

        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")

        # The schedule scope is narrowed, but the untouched approval still
        # covers the archived A+B generation: the redelivery stays authorized
        # by that standing approval.
        sched.filter_tenants.set([tenant_a])
        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)
        self.assertEqual(outcome.code, "retry.completed")
        self.assertEqual(email_retry.call_count, 1)


class ScheduledReportOutcomeSeparationTests(TestCase):
    """Generation, archive, and delivery outcomes are recorded separately."""

    def setUp(self):
        from organization.models import Tenant

        self.tenant = Tenant.objects.create(name="Outcome Tenant", slug="outcome-tenant")
        self.template = ReportTemplate.objects.create(
            name="Outcome Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )

    def _schedule(self, **overrides):
        defaults = dict(
            name="Outcome Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        defaults.update(overrides)
        return ScheduledReport.objects.create(**defaults)

    def test_generation_failure_is_recorded_without_any_delivery_attempt(self):
        from extras.tasks.reports import generate_scheduled_report_task

        sched = self._schedule()
        with (
            mock.patch("extras.tasks.reports.build_report_context", side_effect=RuntimeError("compile failed")),
            mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as deliver_email,
        ):
            result = generate_scheduled_report_task(sched.pk)

        self.assertEqual(result.code, "report.generation_failed")
        deliver_email.assert_not_called()
        sched.refresh_from_db()
        self.assertTrue(sched.last_status.endswith("report.generation_failed"))
        # The archive row, if any, is marked failed but carries no dispatch
        # ledger — generation never reached the delivery fan-out.
        for archive in ReportGenerationArchive.objects.filter(scheduled_report=sched):
            self.assertEqual(archive.status, "failed")
            self.assertEqual(archive.error_message, "report.generation_failed")
            self.assertEqual(archive.delivery_status, "")

    def test_schedule_without_targets_records_none_and_completes_cleanly(self):
        from extras.tasks.reports import generate_scheduled_report_task

        sched = self._schedule(recipients="")
        with mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=True) as channel:
            result = generate_scheduled_report_task(sched.pk)

        self.assertEqual(result.code, "report.completed")
        channel.assert_not_called()
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        self.assertEqual(archive.status, "success")
        self.assertEqual(archive.delivery_status, "none")
        self.assertEqual(archive.delivery_targets, [])
        sched.refresh_from_db()
        # The schedule-level token marks the run clean; the *no targets*
        # distinction lives in the delivery ledger, not in last_status.
        self.assertEqual(sched.last_status, "success")
