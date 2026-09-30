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
* **Summary fencing** — overlapping occurrences are allowed, but schedule-level
  summary writes (``last_status``/``last_run_archive``) are fenced to the newest
  started run: a slower older run finishing late keeps its own archive and
  ledger yet can never overwrite a newer run's summary or retry binding, and a
  retry completion is fenced to its archive still being the newest binding.
* **Recovery** — a failed delivery leaves a per-target ledger and is recovered
  through ``Retry delivery``, which re-contacts only the failed targets with
  the recorded original recipients, email subject/body, and notification
  payloads, re-validates the archived generation scope first, refuses inactive
  schedules, and replays exactly the newest run's own archive (a run that
  retained none is refused instead of falling back to an older report). The
  attempt is claimed atomically, the fan-out renews the lease before every
  target and aborts once the lease was lost, and outbound attempts are
  bounded — overlap is limited to a single in-flight send (best-effort
  duplicate suppression).
* **Storage-failure archival** — a file-write failure after the archive row
  was created marks that archive failed instead of leaving it running.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone
from django.utils.module_loading import import_string
from django_q.conf import Conf
from django_q.models import Schedule
from django_q.scheduler import scheduler
from django_q.signing import SignedPackage

from extras.models import (
    NotificationChannel,
    ReportGenerationArchive,
    ReportTemplate,
    ScheduledReport,
    ScheduledReportFire,
)
from extras.tasks.reports import (
    _parse_intended_fire_at,
    _process_scheduled_report,
    retry_failed_deliveries,
)
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
        fires = ScheduledReportFire.objects.filter(schedule=sched).order_by("intended_fire_at")
        self.assertEqual(fires.count(), 2)
        self.assertEqual(str(fires.first()), f"{sched.pk}@{older_at:%Y-%m-%d %H:%M:%S}")

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

    def test_concurrent_retries_are_serialized_by_the_exclusive_claim(self):
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

        def deliver_and_race(sched_arg, template, output, recipients, subject=None, body=None):
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

    def _cross_tenant_recovery_schedule(self):
        from django.contrib.auth import get_user_model

        from extras.models import ScheduledReportScopeAuthorization
        from organization.models import Tenant

        tenant_a = Tenant.objects.create(name="Replay Scope A", slug="replay-scope-a")
        tenant_b = Tenant.objects.create(name="Replay Scope B", slug="replay-scope-b")
        actor = get_user_model().objects.create_superuser(
            username="replay-scope-approver", password="password123", email="approver@example.com"
        )
        sched = ScheduledReport.objects.create(
            name="Cross-tenant Replay Schedule",
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
        return sched, tenant_a, tenant_b, actor

    def _newest_archive(self, sched):
        return ReportGenerationArchive.objects.filter(scheduled_report=sched).order_by("-generated_at").first()

    def test_replay_refusal_tokens_are_recorded_per_target(self):
        """Deleted, detached, disabled, malformed, and unknown targets all refuse."""
        attached = NotificationChannel.objects.create(
            name="Attached Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        detached = NotificationChannel.objects.create(
            name="Detached Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        disabled = NotificationChannel.objects.create(
            name="Disabled Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=False,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        deleted = NotificationChannel.objects.create(
            name="Deleted Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        sched = ScheduledReport.objects.create(
            name="Refusal Replay Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.channels.set([attached, disabled, deleted])
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_partial")

        NotificationChannel.objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())
        archive = self._newest_archive(sched)
        archive.delivery_targets = [
            {
                "target": f"channel:{deleted.pk}",
                "label": "Deleted",
                "status": "failed",
                "error": "channel.delivery_failed",
            },
            {
                "target": f"channel:{detached.pk}",
                "label": "Detached",
                "status": "failed",
                "error": "channel.delivery_failed",
            },
            {
                "target": f"channel:{disabled.pk}",
                "label": "Disabled",
                "status": "failed",
                "error": "channel.delivery_failed",
            },
            {
                "target": "channel:not-a-number",
                "label": "Malformed",
                "status": "failed",
                "error": "channel.delivery_failed",
            },
            {"target": "webhook:7", "label": "Unknown", "status": "failed", "error": "delivery_failed"},
        ]
        archive.delivery_status = "failed"
        archive.save(update_fields=["delivery_targets", "delivery_status"])

        with mock.patch("extras.tasks.reports.send_notification_to_channel") as sender:
            outcome = retry_failed_deliveries(sched)

        sender.assert_not_called()
        self.assertEqual(outcome.code, "retry.partial")
        self.assertEqual(outcome.retried, 5)
        self.assertEqual(outcome.still_failed, 5)
        archive.refresh_from_db()
        by_target = {target["target"]: target for target in archive.delivery_targets}
        self.assertEqual(by_target[f"channel:{deleted.pk}"]["error"], "channel.missing")
        self.assertEqual(by_target[f"channel:{detached.pk}"]["error"], "channel.detached")
        self.assertEqual(by_target[f"channel:{disabled.pk}"]["error"], "channel.disabled")
        self.assertEqual(by_target["channel:not-a-number"]["error"], "channel.missing")
        self.assertEqual(by_target["webhook:7"]["error"], "retry.unknown_target")

    def test_replay_reconstructs_reduced_payloads_and_recorded_bodies(self):
        """Legacy entries fall back to the reconstruction; partial payloads keep the recorded body."""
        legacy = NotificationChannel.objects.create(
            name="Legacy Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        partial = NotificationChannel.objects.create(
            name="Partial Payload Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        sched = ScheduledReport.objects.create(
            name="Payload Fallback Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.channels.set([legacy, partial])
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_partial")

        archive = self._newest_archive(sched)
        archive.disclosure_text = "Internal use only"
        archive.delivery_targets = [
            {
                "target": f"channel:{legacy.pk}",
                "label": "Legacy",
                "status": "failed",
                "error": "channel.delivery_rejected",
            },
            {
                "target": f"channel:{partial.pk}",
                "label": "Partial",
                "status": "failed",
                "error": "channel.delivery_rejected",
                "details": {"payload": {"body": "Recorded body"}},
            },
        ]
        archive.delivery_status = "failed"
        archive.save(update_fields=["delivery_targets", "delivery_status", "disclosure_text"])

        with mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=True) as sender:
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.completed")
        self.assertEqual(sender.call_count, 2)
        calls = {call.args[0].pk: call.args[1:] for call in sender.call_args_list}
        subject_legacy, body_legacy = calls[legacy.pk]
        self.assertEqual(subject_legacy, f"[Scheduled Report] {sched.name}")
        self.assertIn("is being redelivered", body_legacy)
        self.assertIn("Internal use only", body_legacy)
        subject_partial, body_partial = calls[partial.pk]
        self.assertEqual(subject_partial, f"[Scheduled Report] {sched.name}")
        self.assertEqual(body_partial, "Recorded body")

    def test_retry_email_failures_escalate_from_exception_to_success(self):
        sched = ScheduledReport.objects.create(
            name="Email Escalation Schedule",
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
        self.assertEqual(sched.last_status, "failed")

        with mock.patch("extras.tasks.reports._deliver_report_email", side_effect=RuntimeError("still down")):
            first = retry_failed_deliveries(sched)
        self.assertEqual(first.code, "retry.partial")
        archive = self._newest_archive(sched)
        archive.refresh_from_db()
        email_target = next(target for target in archive.delivery_targets if target["target"] == "email")
        self.assertEqual(email_target["error"], "email.delivery_failed")

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=False):
            second = retry_failed_deliveries(sched)
        self.assertEqual(second.code, "retry.partial")
        archive.refresh_from_db()
        email_target = next(target for target in archive.delivery_targets if target["target"] == "email")
        self.assertEqual(email_target["error"], "email.delivery_rejected")
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "failed")

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True):
            third = retry_failed_deliveries(sched)
        self.assertEqual(third.code, "retry.completed")
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "success")

    def test_retry_channel_failures_escalate_from_exception_to_success(self):
        channel = NotificationChannel.objects.create(
            name="Escalation Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        sched = ScheduledReport.objects.create(
            name="Channel Escalation Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.channels.set([channel])
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"), channel_result=False)
        self.assertEqual(result.code, "report.delivery_failed")

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", return_value=True),
            mock.patch("extras.tasks.reports.send_notification_to_channel", side_effect=RuntimeError("channel down")),
        ):
            first = retry_failed_deliveries(sched)
        self.assertEqual(first.code, "retry.partial")
        archive = self._newest_archive(sched)
        archive.refresh_from_db()
        entry = next(target for target in archive.delivery_targets if target["target"] == f"channel:{channel.pk}")
        self.assertEqual(entry["error"], "channel.delivery_failed")

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", return_value=True),
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=False),
        ):
            second = retry_failed_deliveries(sched)
        self.assertEqual(second.code, "retry.partial")
        archive.refresh_from_db()
        entry = next(target for target in archive.delivery_targets if target["target"] == f"channel:{channel.pk}")
        self.assertEqual(entry["error"], "channel.delivery_rejected")

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", return_value=True),
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=True),
        ):
            third = retry_failed_deliveries(sched)
        self.assertEqual(third.code, "retry.completed")

    def test_a_superseded_retry_completion_leaves_the_newer_state_in_place(self):
        """A lease takeover mid-flight must not be clobbered by the stale attempt."""
        sched = ScheduledReport.objects.create(
            name="Superseded Claim Schedule",
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
        archive = self._newest_archive(sched)

        def hijacked_delivery(sched_arg, template, output, recipients, subject=None, body=None):
            # A newer attempt took the claim over while this one was mid-flight.
            ReportGenerationArchive.objects.filter(pk=archive.pk).update(
                retry_claim_token="taken-over",
                retry_claim_expires_at=timezone.now() + timedelta(minutes=10),
            )
            return True

        with mock.patch("extras.tasks.reports._deliver_report_email", side_effect=hijacked_delivery):
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.completed")
        archive.refresh_from_db()
        self.assertEqual(archive.retry_claim_token, "taken-over")
        self.assertEqual([target["status"] for target in archive.delivery_targets], ["failed"])

    def test_retry_reports_no_retained_output_for_unreadable_files(self):
        sched = ScheduledReport.objects.create(
            name="Unreadable Archive Schedule",
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
        archive = self._newest_archive(sched)
        attachment = archive.file
        attachment.file.storage.delete(attachment.file.name)

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.no_retained_output")
        email_retry.assert_not_called()

    def test_retry_legacy_archive_without_a_snapshot_uses_the_current_scope_check(self):
        from extras.models import ScheduledReportScopeAuthorization

        sched, _tenant_a, _tenant_b, actor = self._cross_tenant_recovery_schedule()
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")
        archive = self._newest_archive(sched)
        archive.generation_scope = {}
        archive.save(update_fields=["generation_scope"])

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.completed")
        self.assertEqual(email_retry.call_count, 1)

        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=sched)
        authorization.revoked_at = timezone.now()
        authorization.revoked_by = actor
        authorization.save(update_fields=["revoked_at", "revoked_by"])
        archive.refresh_from_db()
        archive.delivery_targets = [
            {
                "target": "email",
                "label": "Email",
                "status": "failed",
                "error": "email.delivery_failed",
                "details": {"recipients": ["ops@example.com"]},
            }
        ]
        archive.delivery_status = "failed"
        archive.save(update_fields=["delivery_targets", "delivery_status"])

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as refused_retry:
            refused = retry_failed_deliveries(sched)

        self.assertEqual(refused.code, "retry.scope_unauthorized")
        refused_retry.assert_not_called()

    def test_retry_snapshot_scope_fails_closed_without_a_resolvable_scope(self):
        sched, _tenant_a, _tenant_b, _actor = self._cross_tenant_recovery_schedule()
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")
        archive = self._newest_archive(sched)

        with mock.patch("extras.tasks.reports._resolve_report_scope", return_value=None):
            with_snapshot = retry_failed_deliveries(sched)
        self.assertEqual(with_snapshot.code, "retry.scope_unauthorized")

        archive.generation_scope = {}
        archive.save(update_fields=["generation_scope"])
        with mock.patch("extras.tasks.reports._resolve_report_scope", return_value=None):
            legacy = retry_failed_deliveries(sched)
        self.assertEqual(legacy.code, "retry.scope_unauthorized")

    def test_retry_snapshot_scope_requires_the_same_active_tenant(self):
        sched = ScheduledReport.objects.create(
            name="Drifted Tenant Schedule",
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
        archive = self._newest_archive(sched)
        archive.generation_scope = {
            "active_tenant_id": 987654321,
            "cross_tenant": False,
            "data_tenant_ids": [987654321],
        }
        archive.save(update_fields=["generation_scope"])

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.scope_unauthorized")
        email_retry.assert_not_called()

    def test_retry_snapshot_scope_fails_closed_on_anomalies(self):
        from organization.models import Tenant

        sched, tenant_a, tenant_b, actor = self._cross_tenant_recovery_schedule()
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")
        archive = self._newest_archive(sched)

        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            cases = (
                ({"active_tenant_id": None, "cross_tenant": True, "data_tenant_ids": ["junk"]}, "malformed ids"),
                ({"active_tenant_id": None, "cross_tenant": True, "data_tenant_ids": []}, "no recorded tenants"),
            )
            for snapshot, label in cases:
                with self.subTest(label):
                    archive.generation_scope = snapshot
                    archive.save(update_fields=["generation_scope"])
                    outcome = retry_failed_deliveries(sched)
                    self.assertEqual(outcome.code, "retry.scope_unauthorized")

            with self.subTest("deactivated principal"):
                archive.generation_scope = {
                    "active_tenant_id": None,
                    "cross_tenant": True,
                    "data_tenant_ids": sorted([tenant_a.pk, tenant_b.pk]),
                }
                archive.save(update_fields=["generation_scope"])
                actor.is_active = False
                actor.save(update_fields=["is_active"])
                outcome = retry_failed_deliveries(sched)
                self.assertEqual(outcome.code, "retry.scope_unauthorized")
                actor.is_active = True
                actor.save(update_fields=["is_active"])

            with self.subTest("soft-deleted tenant"):
                Tenant.objects.filter(pk=tenant_b.pk).update(deleted_at=timezone.now())
                outcome = retry_failed_deliveries(sched)
                self.assertEqual(outcome.code, "retry.scope_unauthorized")

        email_retry.assert_not_called()

    def test_retry_is_refused_when_the_failed_run_retained_no_archive(self):
        # Run A is archived and fails delivery; then ``save_to_archive`` is
        # switched off, so run B fails without creating an archive. Retry
        # must never fall back to re-sending run A's older report.
        sched = ScheduledReport.objects.create(
            name="Unarchived Recovery Schedule",
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
        first_archive = self._newest_archive(sched)
        self.assertIsNotNone(first_archive)

        sched.save_to_archive = False
        sched.save(update_fields=["save_to_archive"])
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"))
        self.assertEqual(result.code, "report.delivery_failed")
        # The unarchived run created nothing: the first archive stays alone.
        self.assertEqual(ReportGenerationArchive.objects.filter(scheduled_report=sched).count(), 1)

        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "failed")
        # The last run left no archive, so the recovery action is withheld
        # and the service refuses instead of re-sending the older report.
        self.assertIsNone(sched.last_run_archive_id)
        self.assertFalse(sched.delivery_retryable)
        with mock.patch("extras.tasks.reports._deliver_report_email", return_value=True) as email_retry:
            outcome = retry_failed_deliveries(sched)
        self.assertEqual(outcome.code, "retry.no_archive")
        email_retry.assert_not_called()
        first_archive.refresh_from_db()
        # Run A's archive and its ledger are untouched by the refused retry.
        self.assertEqual(first_archive.delivery_status, "failed")

    def test_retry_aborts_the_fan_out_when_its_claim_lease_is_stolen(self):
        channel = NotificationChannel.objects.create(
            name="Lease Steal Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        sched = ScheduledReport.objects.create(
            name="Lease Renewal Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.channels.add(channel)
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"), channel_result=False)
        self.assertEqual(result.code, "report.delivery_failed")
        archive = self._newest_archive(sched)

        def stealing_delivery(sched_arg, template, output, recipients, subject=None, body=None):
            # A newer attempt takes the claim over while this delivery is in
            # flight; the next lease renewal must notice and abort.
            ReportGenerationArchive.objects.filter(pk=archive.pk).update(
                retry_claim_token="taken-over",
                retry_claim_expires_at=timezone.now() + timedelta(minutes=10),
            )
            return True

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", side_effect=stealing_delivery),
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=True) as channel_send,
        ):
            outcome = retry_failed_deliveries(sched)

        # The email went out (its renewal preceded the steal); the channel
        # fan-out is aborted because the lease was lost.
        self.assertEqual(outcome.code, "retry.partial")
        self.assertEqual(outcome.retried, 1)
        channel_send.assert_not_called()
        archive.refresh_from_db()
        self.assertEqual(archive.retry_claim_token, "taken-over")
        # The superseded attempt leaves the newer state in place.
        self.assertEqual([target["status"] for target in archive.delivery_targets], ["failed", "failed"])

    def test_retry_replays_the_recorded_email_subject_and_body(self):
        sched = ScheduledReport.objects.create(
            name="Payload Replay Schedule",
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
        archive = self._newest_archive(sched)
        recorded = next(
            target["details"]["payload"] for target in archive.delivery_targets if target["target"] == "email"
        )
        self.assertIn("Payload Replay Schedule", recorded["subject"])
        self.assertTrue(recorded["body"])

        # The schedule is edited after the failed run; the retry must still
        # send the recorded original subject and body.
        sched.name = "Renamed After Failure"
        sched.save(update_fields=["name"])

        captured = {}

        def capturing_delivery(sched_arg, template, output, recipients, subject=None, body=None):
            captured["subject"] = subject
            captured["body"] = body
            return True

        with mock.patch("extras.tasks.reports._deliver_report_email", side_effect=capturing_delivery):
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.completed")
        self.assertEqual(captured["subject"], recorded["subject"])
        self.assertEqual(captured["body"], recorded["body"])
        self.assertIn("Payload Replay Schedule", captured["subject"])
        self.assertNotIn("Renamed After Failure", captured["subject"])

    def test_storage_failure_marks_the_created_archive_failed(self):
        sched = ScheduledReport.objects.create(
            name="Storage Failure Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        with mock.patch(
            "extras.tasks.reports.FileAttachment.objects.create",
            side_effect=RuntimeError("disk full"),
        ):
            result, channel = self._deliver_once(sched, email_effect=True)

        self.assertEqual(result.code, "report.generation_failed")
        channel.assert_not_called()
        sched.refresh_from_db()
        self.assertTrue(sched.last_status.endswith("report.generation_failed"))
        # The persisted archive is marked failed instead of staying running.
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        self.assertEqual(archive.status, "failed")
        self.assertEqual(archive.error_message, "report.generation_failed")
        # No retry binding is kept for the failed run.
        self.assertIsNone(sched.last_run_archive_id)

    def test_a_slower_older_run_never_overwrites_the_newer_summary(self):
        sched = ScheduledReport.objects.create(
            name="Overlap Summary Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        # Run B (the newer started run) completes first, with success.
        result_b, _channel = self._deliver_once(sched, email_effect=True)
        self.assertEqual(result_b.code, "report.completed")
        sched.refresh_from_db()
        newer_start = sched.last_run
        newer_archive = sched.last_run_archive
        self.assertEqual(sched.last_status, "success")
        self.assertIsNotNone(newer_archive)

        # Run A overlapped: its start marker is older than B's, and it only
        # finishes now, with a delivery failure. Its own archive and ledger
        # must be complete, but the schedule summary must keep B's state.
        older_started_at = newer_start - timedelta(minutes=5)
        with mock.patch(
            "extras.tasks.reports._deliver_report_email",
            side_effect=RuntimeError("smtp down"),
        ):
            success = _process_scheduled_report(sched, self.tenant, [], run_started_at=older_started_at)

        self.assertFalse(success)
        sched.refresh_from_db()
        self.assertEqual(sched.last_run, newer_start)
        self.assertEqual(sched.last_status, "success")
        self.assertEqual(sched.last_run_archive_id, newer_archive.pk)
        self.assertFalse(sched.delivery_retryable)
        older_archive = (
            ReportGenerationArchive.objects.filter(scheduled_report=sched).exclude(pk=newer_archive.pk).get()
        )
        # The older run's own archive and per-target ledger are complete.
        self.assertEqual(older_archive.delivery_status, "failed")
        self.assertEqual([target["status"] for target in older_archive.delivery_targets], ["failed"])

    def test_an_older_runs_generation_failure_cannot_reset_the_newer_summary(self):
        sched = ScheduledReport.objects.create(
            name="Overlap Failure Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        result_b, _channel = self._deliver_once(sched, email_effect=True)
        self.assertEqual(result_b.code, "report.completed")
        sched.refresh_from_db()
        newer_start = sched.last_run
        newer_archive = sched.last_run_archive
        self.assertEqual(sched.last_status, "success")

        older_started_at = newer_start - timedelta(minutes=5)
        with mock.patch(
            "extras.tasks.reports._render_report_output",
            side_effect=RuntimeError("render blew up"),
        ):
            result_a = _process_scheduled_report(sched, self.tenant, [], run_started_at=older_started_at)

        self.assertEqual(result_a.code, "report.generation_failed")
        sched.refresh_from_db()
        # The older failed run neither resets the start marker nor clears the
        # newer run's status, retry binding, or its sole archive.
        self.assertEqual(sched.last_run, newer_start)
        self.assertEqual(sched.last_status, "success")
        self.assertEqual(sched.last_run_archive_id, newer_archive.pk)
        self.assertEqual(ReportGenerationArchive.objects.filter(scheduled_report=sched).count(), 1)

    def test_a_retry_completion_cannot_overwrite_a_newer_runs_summary(self):
        channel = NotificationChannel.objects.create(
            name="Retry Fence Channel",
            channel_type=NotificationChannel.TYPE_EMAIL,
            enabled=True,
            tenant=self.tenant,
            config={"recipients": "ops@example.com"},
        )
        sched = ScheduledReport.objects.create(
            name="Retry Fence Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            recipients="ops@example.com",
            save_to_archive=True,
            is_active=True,
        )
        sched.channels.add(channel)
        result, _channel = self._deliver_once(sched, email_effect=RuntimeError("smtp down"), channel_result=False)
        self.assertEqual(result.code, "report.delivery_failed")
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "failed")
        retry_archive = self._newest_archive(sched)

        # While the retry is in flight, a newer run completes and rebinds the
        # summary to its own archive and status.
        rebound = []

        def rebinding_delivery(sched_arg, template, output, recipients, subject=None, body=None):
            newer_archive = ReportGenerationArchive.objects.create(
                scheduled_report=sched,
                format=ScheduledReport.FORMAT_HTML,
                status="success",
                tenant=self.tenant,
            )
            ScheduledReport.objects.filter(pk=sched.pk).update(
                last_run=timezone.now(),
                last_run_archive=newer_archive,
                last_status="success",
            )
            rebound.append(newer_archive)
            return True

        with (
            mock.patch("extras.tasks.reports._deliver_report_email", side_effect=rebinding_delivery),
            mock.patch("extras.tasks.reports.send_notification_to_channel", return_value=False),
        ):
            outcome = retry_failed_deliveries(sched)

        self.assertEqual(outcome.code, "retry.partial")
        sched.refresh_from_db()
        # The retry keeps its own archive and ledger, but the schedule summary
        # belongs to the newer run.
        self.assertEqual(sched.last_status, "success")
        self.assertEqual(sched.last_run_archive_id, rebound[0].pk)
        retry_archive.refresh_from_db()
        self.assertEqual(retry_archive.delivery_status, "partial")
        self.assertFalse(retry_archive.retry_claim_token)


class IntendedFireParsingTests(SimpleTestCase):
    """The scheduler-injected occurrence timestamp is parsed defensively."""

    def test_parses_iso_strings_and_datetimes_and_ignores_garbage(self):
        naive = datetime(2026, 9, 29, 12, 0, 0)
        aware = timezone.make_aware(naive, timezone.get_current_timezone())

        self.assertEqual(_parse_intended_fire_at(aware.isoformat()), aware)
        self.assertEqual(_parse_intended_fire_at(aware), aware)
        self.assertIsNone(_parse_intended_fire_at("not-a-timestamp"))

        parsed_naive = _parse_intended_fire_at(naive)
        self.assertTrue(timezone.is_aware(parsed_naive))
        self.assertEqual(timezone.localtime(parsed_naive).replace(tzinfo=None), naive)


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
