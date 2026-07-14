"""Tests for the changelog / operational-data retention lifecycle.

Covers `prune_changelog` (core/management/commands/prune_changelog.py):
retention boundaries, the 0=unlimited convention, per-tenant changelog
overrides (including legal hold), --tenant restriction, --dry-run, and
batched deletes across multiple batches. `core.tasks.prune_changelog_task`
is a thin `call_command` wrapper (core/tasks/retention.py) exercised
indirectly via a dedicated smoke test rather than duplicating command
coverage.
"""
import io
import uuid
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from django_q.models import Failure

from core.choices import ObjectChangeActionChoices
from core.models import Notification, ObjectChange
from extras.models import AlertLog, AlertRule
from organization.models import Tenant


class PruneChangelogCommandTests(TestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')
        self.ct = ContentType.objects.get_for_model(Tenant)
        self.now = timezone.now()

    def _run(self, **kwargs):
        call_command('prune_changelog', stdout=self.stdout, stderr=self.stderr, **kwargs)

    def _make_change(self, *, tenant, age_days):
        return ObjectChange._base_manager.create(
            tenant=tenant,
            user=None,
            user_name='system',
            request_id=uuid.uuid4(),
            action=ObjectChangeActionChoices.ACTION_UPDATE,
            changed_object_type=self.ct,
            changed_object_id=1,
            object_repr='Test Row',
            time=self.now - timedelta(days=age_days),
        )

    def _exists(self, row):
        return ObjectChange._base_manager.filter(pk=row.pk).exists()

    # -- retention boundary ------------------------------------------------

    def test_retention_boundary_prunes_older_keeps_newer(self):
        old = self._make_change(tenant=None, age_days=31)
        new = self._make_change(tenant=None, age_days=29)

        self._run(classes='changelog', changelog_days=30)

        self.assertFalse(self._exists(old))
        self.assertTrue(self._exists(new))

    # -- 0 means unlimited ---------------------------------------------------

    def test_zero_retention_means_unlimited(self):
        ancient = self._make_change(tenant=None, age_days=5000)

        self._run(classes='changelog', changelog_days=0)

        self.assertTrue(self._exists(ancient))

    # -- tenant override wins over global -------------------------------------

    def test_tenant_override_wins_over_global(self):
        self.tenant_a.changelog_retention_days = 10
        self.tenant_a.save(update_fields=['changelog_retention_days'])

        tenant_row = self._make_change(tenant=self.tenant_a, age_days=15)
        other_row = self._make_change(tenant=self.tenant_b, age_days=15)

        # Global window (365d) alone would keep both rows; tenant_a's 10-day
        # override must still prune its own row.
        self._run(classes='changelog', changelog_days=365)

        self.assertFalse(self._exists(tenant_row))
        self.assertTrue(self._exists(other_row))

    # -- legal hold: override 0 never pruned ----------------------------------

    def test_legal_hold_override_never_pruned(self):
        self.tenant_a.changelog_retention_days = 0
        self.tenant_a.save(update_fields=['changelog_retention_days'])

        held_row = self._make_change(tenant=self.tenant_a, age_days=5000)

        self._run(classes='changelog', changelog_days=30)

        self.assertTrue(self._exists(held_row))

    # -- --tenant restriction --------------------------------------------------

    def test_tenant_restriction_leaves_other_tenants_and_global_rows_untouched(self):
        row_a = self._make_change(tenant=self.tenant_a, age_days=100)
        row_b = self._make_change(tenant=self.tenant_b, age_days=100)
        row_global = self._make_change(tenant=None, age_days=100)

        self._run(classes='changelog', changelog_days=1, tenant=self.tenant_a.slug)

        self.assertFalse(self._exists(row_a))
        self.assertTrue(self._exists(row_b))
        self.assertTrue(self._exists(row_global))

    # -- dry run mutates nothing -----------------------------------------------

    def test_dry_run_mutates_nothing(self):
        ancient = self._make_change(tenant=None, age_days=5000)

        self._run(classes='changelog', changelog_days=30, dry_run=True)

        self.assertTrue(self._exists(ancient))
        self.assertIn('DRY RUN', self.stdout.getvalue())

    # -- batching terminates across multiple batches ----------------------------

    def test_batching_loop_terminates_across_multiple_batches(self):
        rows = [self._make_change(tenant=None, age_days=100 + i) for i in range(5)]

        # batch_size=2 forces 3 batches (2, 2, 1) for 5 matching rows; the test
        # itself finishing is proof the loop terminates rather than looping
        # forever re-selecting an already-drained batch.
        self._run(classes='changelog', changelog_days=1, batch_size=2)

        for row in rows:
            self.assertFalse(self._exists(row))
        self.assertIn('done: 5 row(s) pruned', self.stdout.getvalue())

    # -- unknown --tenant slug --------------------------------------------------

    def test_unknown_tenant_slug_raises(self):
        from django.core.management import CommandError

        with self.assertRaises(CommandError):
            self._run(classes='changelog', tenant='does-not-exist')

    # -- the other three data classes -------------------------------------------

    def test_prunes_alertlog_notification_and_qtask_against_own_settings(self):
        rule = AlertRule._base_manager.create(
            name='Retention Test Rule',
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
        )

        old_alert = AlertLog._base_manager.create(
            rule=rule, content_type=self.ct, object_id=1, subject='old', message='old',
        )
        AlertLog._base_manager.filter(pk=old_alert.pk).update(created_at=self.now - timedelta(days=200))
        # object_id=2: a second OPEN alert for the same (rule, object) would trip
        # the uniq_open_alert_per_object constraint — retention only cares about age.
        new_alert = AlertLog._base_manager.create(
            rule=rule, content_type=self.ct, object_id=2, subject='new', message='new',
        )
        AlertLog._base_manager.filter(pk=new_alert.pk).update(created_at=self.now - timedelta(days=1))

        old_notif = Notification.objects.create(subject='old', message='old')
        Notification.objects.filter(pk=old_notif.pk).update(created_at=self.now - timedelta(days=100))
        new_notif = Notification.objects.create(subject='new', message='new')
        Notification.objects.filter(pk=new_notif.pk).update(created_at=self.now - timedelta(days=1))

        old_failure = Failure.objects.create(
            id=uuid.uuid4().hex, name='old-fail', func='x.y.z',
            started=self.now - timedelta(days=100), stopped=self.now - timedelta(days=100),
            success=False,
        )
        new_failure = Failure.objects.create(
            id=uuid.uuid4().hex, name='new-fail', func='x.y.z',
            started=self.now - timedelta(days=1), stopped=self.now - timedelta(days=1),
            success=False,
        )

        self._run(
            classes='alertlog,notification,qtask',
            alertlog_days=30, notification_days=30, qtask_days=30,
        )

        self.assertFalse(AlertLog._base_manager.filter(pk=old_alert.pk).exists())
        self.assertTrue(AlertLog._base_manager.filter(pk=new_alert.pk).exists())
        self.assertFalse(Notification.objects.filter(pk=old_notif.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=new_notif.pk).exists())
        self.assertFalse(Failure.objects.filter(pk=old_failure.pk).exists())
        self.assertTrue(Failure.objects.filter(pk=new_failure.pk).exists())


class PruneChangelogTaskTests(TestCase):
    """Smoke test for the django-q2 scheduled wrapper (core/tasks/retention.py)."""

    def test_prune_changelog_task_delegates_to_command(self):
        from core.tasks import prune_changelog_task

        ct = ContentType.objects.get_for_model(Tenant)
        old = ObjectChange._base_manager.create(
            tenant=None, user=None, user_name='system', request_id=uuid.uuid4(),
            action=ObjectChangeActionChoices.ACTION_UPDATE, changed_object_type=ct,
            changed_object_id=1, object_repr='Test Row',
            time=timezone.now() - timedelta(days=5000),
        )

        with self.settings(ITAMBOX_CHANGELOG_RETENTION_DAYS=30):
            prune_changelog_task()

        self.assertFalse(ObjectChange._base_manager.filter(pk=old.pk).exists())
