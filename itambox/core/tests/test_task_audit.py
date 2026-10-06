"""
Phase 3 audit-trail regression tests for background tasks.

Background tasks previously set the tenant context but not the change-logging
contextvars (request_id / current_user), so ChangeLoggingMixin silently skipped
logging for saves performed inside the task (it bails out when request_id is
None). Both tasks under test now wrap their per-object work in TaskContext, which
wires up tenant + request_id + current_user, so saves are recorded as
ObjectChange rows. Q_CLUSTER['sync'] is True under tests, so tasks run inline.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TransactionTestCase
from django.utils import timezone

from assets.models import Supplier
from core.models import ObjectChange
from extras.models import ReportGenerationArchive, ReportTemplate, ScheduledReport
from extras.tasks.reports import generate_scheduled_report_task
from subscriptions.models import (
    Subscription,
    SubscriptionStatusChoices,
)
from subscriptions.tasks import check_subscription_expiries_and_reminders

User = get_user_model()


class ScheduledReportTaskAuditTests(TransactionTestCase):
    """generate_scheduled_report_task must run inside TaskContext so the model
    saves it performs are recorded as ObjectChange rows. The per-run archive is
    such a save; the schedule's own start marker and summary writes are
    deliberate atomic queryset updates (overlap race fences), so the
    ScheduledReport itself gains no ObjectChange rows from a run."""

    def test_scheduled_report_run_logs_object_change(self):
        from organization.models import Tenant

        tenant = Tenant.objects.create(name="Phase3 Tenant", slug="phase3-report-tenant")
        template = ReportTemplate.objects.create(
            name="Phase3 Audit Asset Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            tenant=tenant,
        )
        sched = ScheduledReport.objects.create(
            name="Phase3 Audit Schedule",
            report=template,
            tenant=tenant,
            format=ScheduledReport.FORMAT_HTML,
            save_to_archive=True,
            recipients="",
            is_active=True,
        )

        sched_ct = ContentType.objects.get_for_model(ScheduledReport)
        archive_ct = ContentType.objects.get_for_model(ReportGenerationArchive)
        # Fixture creation above happened outside any task/request context, so it
        # was NOT logged. Baselines should therefore be zero for these objects.
        sched_baseline = ObjectChange.objects.filter(changed_object_type=sched_ct, changed_object_id=sched.pk).count()

        result = generate_scheduled_report_task(sched.pk)
        self.assertTrue(result)

        sched.refresh_from_db()
        self.assertIsNotNone(sched.last_run)
        self.assertEqual(sched.last_status, "success")

        # The run's model saves (the generation archive row) are audited...
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        archive_changes = ObjectChange.objects.filter(
            changed_object_type=archive_ct, changed_object_id=archive.pk
        ).count()
        self.assertGreater(
            archive_changes,
            0,
            "generate_scheduled_report_task should record ObjectChange rows for "
            "the saves it performs (running inside TaskContext).",
        )
        # ...while the schedule's race-fenced marker/summary writes are atomic
        # queryset updates and deliberately not model saves.
        sched_after = ObjectChange.objects.filter(changed_object_type=sched_ct, changed_object_id=sched.pk).count()
        self.assertEqual(sched_after, sched_baseline)


class SubscriptionExpiryTaskAuditTests(TransactionTestCase):
    """check_subscription_expiries_and_reminders must mark expired subscriptions
    AND log the change (each subscription runs inside its own TaskContext bound
    to its tenant)."""

    def test_expiry_marks_subscription_and_logs_change(self):
        supplier = Supplier.objects.create(name="Phase3 Audit Vendor")
        today = timezone.now().date()
        future = today + datetime.timedelta(days=30)
        past = today - datetime.timedelta(days=1)

        # Create the subscription with a FUTURE renewal_date so the pre_save
        # auto-expiry signal leaves it ACTIVE...
        sub = Subscription.objects.create(
            name="Phase3 Audit Subscription",
            supplier=supplier,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=future,
        )
        # ...then backdate via .update() (bypasses the pre_save signal) so the
        # task is the one that flips it to EXPIRED.
        Subscription.objects.filter(pk=sub.pk).update(renewal_date=past)
        sub.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatusChoices.ACTIVE)

        sub_ct = ContentType.objects.get_for_model(Subscription)
        baseline = ObjectChange.objects.filter(changed_object_type=sub_ct, changed_object_id=sub.pk).count()

        check_subscription_expiries_and_reminders()

        sub.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatusChoices.EXPIRED)

        changes = ObjectChange.objects.filter(changed_object_type=sub_ct, changed_object_id=sub.pk)
        self.assertGreater(
            changes.count(),
            baseline,
            "Auto-expiry must record an ObjectChange for the Subscription "
            "status change (now running inside TaskContext).",
        )
