"""Tests for Alert Center bulk acknowledge/resolve views."""
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from extras.models import AlertRule, AlertLog

User = get_user_model()


class AlertBulkActionViewTests(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser(
            username='bulkadmin', password='x', email='a@b.com'
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.rule = AlertRule.objects.create(
            name='Bulk Rule', alert_type=AlertRule.ALERT_TYPE_LOW_STOCK, threshold_value=5,
        )
        self.ct = ContentType.objects.get_for_model(AlertRule)

    def _log(self, oid, status=AlertLog.STATUS_ACTIVE):
        return AlertLog.objects.create(
            rule=self.rule, subject='s', message='m',
            content_type=self.ct, object_id=oid, status=status,
        )

    def test_bulk_acknowledge_transitions_active_logs(self):
        l1, l2 = self._log(1), self._log(2)
        resp = self.client.post(
            reverse('extras:alertlog_bulk_acknowledge'), {'pk': [l1.pk, l2.pk]}
        )
        self.assertEqual(resp.status_code, 302)
        l1.refresh_from_db(); l2.refresh_from_db()
        self.assertEqual(l1.status, AlertLog.STATUS_ACKNOWLEDGED)
        self.assertEqual(l2.status, AlertLog.STATUS_ACKNOWLEDGED)
        self.assertEqual(l1.acknowledged_by, self.admin)

    def test_bulk_resolve_transitions_active_and_acknowledged(self):
        active = self._log(1, status=AlertLog.STATUS_ACTIVE)
        ack = self._log(2, status=AlertLog.STATUS_ACKNOWLEDGED)
        resp = self.client.post(
            reverse('extras:alertlog_bulk_resolve'), {'pk': [active.pk, ack.pk]}
        )
        self.assertEqual(resp.status_code, 302)
        active.refresh_from_db(); ack.refresh_from_db()
        self.assertEqual(active.status, AlertLog.STATUS_RESOLVED)
        self.assertEqual(ack.status, AlertLog.STATUS_RESOLVED)
        self.assertEqual(active.resolved_by, self.admin)
        self.assertIsNotNone(active.resolved_at)

    def test_bulk_acknowledge_skips_resolved(self):
        resolved = self._log(1, status=AlertLog.STATUS_RESOLVED)
        self.client.post(reverse('extras:alertlog_bulk_acknowledge'), {'pk': [resolved.pk]})
        resolved.refresh_from_db()
        # Resolved alerts are not eligible for acknowledgement.
        self.assertEqual(resolved.status, AlertLog.STATUS_RESOLVED)

    def test_bulk_action_with_no_selection_redirects(self):
        resp = self.client.post(reverse('extras:alertlog_bulk_resolve'), {})
        self.assertEqual(resp.status_code, 302)

    def test_bulk_acknowledge_htmx_returns_trigger(self):
        l1 = self._log(1)
        resp = self.client.post(
            reverse('extras:alertlog_bulk_acknowledge'), {'pk': [l1.pk]},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(resp.status_code, 204)
        self.assertIn('tableRefreshRequired', resp['HX-Trigger'])
        l1.refresh_from_db()
        self.assertEqual(l1.status, AlertLog.STATUS_ACKNOWLEDGED)
