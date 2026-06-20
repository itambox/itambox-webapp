import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from django.test import TransactionTestCase
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.db import transaction

from core.models import Notification
from extras.models import NotificationChannel
from extras.models import Event, EventRule, WebhookEndpoint
from core.events import dispatch_event, send_notification_to_channel
from assets.models import Manufacturer

class EventsSystemTestCase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.manufacturer_ct = ContentType.objects.get_for_model(Manufacturer)

    @patch('requests.request')
    def test_event_dispatch_on_create_update_delete(self, mock_request):
        # Create
        mfr = Manufacturer.objects.create(name="Lenovo", slug="lenovo")
        
        # Verify event was dispatched (should have created an Event)
        event_create = Event.objects.filter(model=self.manufacturer_ct, object_id=mfr.pk, action='create').first()
        self.assertIsNotNone(event_create)
        self.assertEqual(event_create.data, {'app_label': 'assets', 'model_name': 'manufacturer'})

        # Update
        mfr.name = "Lenovo Inc"
        mfr.save()
        event_update = Event.objects.filter(model=self.manufacturer_ct, object_id=mfr.pk, action='update').first()
        self.assertIsNotNone(event_update)

        # Delete
        mfr_pk = mfr.pk
        mfr.delete()
        event_delete = Event.objects.filter(model=self.manufacturer_ct, object_id=mfr_pk, action='delete').first()
        self.assertIsNotNone(event_delete)

    def test_event_rule_conditions_evaluation(self):
        # Create a rule with an "and" condition
        rule = EventRule.objects.create(
            name="Test Rule with Conditions",
            model=self.manufacturer_ct,
            events=['create'],
            action_type=EventRule.ACTION_NOTIFICATION,
            action_config={
                'level': 'warning',
                'subject': 'Alert: {event.action} on {event.model.model}',
                'body': 'Details: {data}'
            },
            conditions={
                'type': 'and',
                'rules': [
                    {'field': 'model_name', 'op': 'eq', 'value': 'manufacturer'},
                    {'field': 'app_label', 'op': 'contains', 'value': 'asset'}
                ]
            },
            enabled=True
        )

        # Fire event manually
        event = Event.objects.create(
            model=self.manufacturer_ct,
            object_id=999,
            action='create',
            data={'app_label': 'assets', 'model_name': 'manufacturer'},
        )
        # Should match and trigger a notification
        dispatch_event(Manufacturer, event, 'create')
        
        notification = Notification.objects.filter(level='warning').first()
        self.assertIsNotNone(notification)
        self.assertIn("manufacturer", notification.subject)
        self.assertIn("assets", notification.message)

    def test_event_rules_scoped_to_instance_tenant_in_system_context(self):
        """WS5-1: in a system context (no active tenant/user) a save must fire ONLY the
        rules belonging to the saved object's OWN tenant (plus global rules), never every
        tenant's rules. Reproduces the cross-tenant dispatch the unscoped manager allowed.
        Also covers WS5-2: a tenant rule's notification fans out to the rule's members, not a
        global user=None broadcast."""
        from django.contrib.auth import get_user_model
        from organization.models import Tenant, Location, TenantRole, TenantMembership
        from core.managers import set_current_tenant

        tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        member_a = get_user_model().objects.create_user(username='member_a', password='pw')
        TenantMembership.objects.create(
            user=member_a, tenant=tenant_a,
            role=TenantRole.objects.create(tenant=tenant_a, name='R', permissions=[]),
        )
        loc_ct = ContentType.objects.get_for_model(Location)

        EventRule.objects.create(
            name="A rule", tenant=tenant_a, model=loc_ct, events=['create'],
            action_type=EventRule.ACTION_NOTIFICATION,
            action_config={'subject': 'A-FIRED', 'body': 'x'}, enabled=True,
        )
        EventRule.objects.create(
            name="B rule", tenant=tenant_b, model=loc_ct, events=['create'],
            action_type=EventRule.ACTION_NOTIFICATION,
            action_config={'subject': 'B-FIRED', 'body': 'x'}, enabled=True,
        )
        EventRule.objects.create(
            name="Global rule", tenant=None, model=loc_ct, events=['create'],
            action_type=EventRule.ACTION_NOTIFICATION,
            action_config={'subject': 'GLOBAL-FIRED', 'body': 'x'}, enabled=True,
        )

        # A Location owned by tenant A, dispatched in a no-tenant / no-user system context.
        set_current_tenant(None)
        loc = Location(name="Site A", tenant=tenant_a)
        loc.pk = 987654  # dispatch only needs pk + tenant_id; no real save required

        dispatch_event(Location, loc, 'create')

        # WS5-2: tenant-A rule fans out to tenant-A members (not a global user=None row).
        self.assertTrue(Notification.objects.filter(subject='A-FIRED', user=member_a).exists())
        self.assertFalse(Notification.objects.filter(subject='A-FIRED', user__isnull=True).exists())
        # A truly global (tenant=None) rule still broadcasts as user=None.
        self.assertTrue(Notification.objects.filter(subject='GLOBAL-FIRED', user__isnull=True).exists())
        self.assertFalse(
            Notification.objects.filter(subject='B-FIRED').exists(),
            "Tenant B's event rule must NOT fire for a tenant-A object in a system context.",
        )

    @patch('requests.request')
    def test_webhook_delivery_with_hmac_signature(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        # Create webhook rule
        rule = EventRule.objects.create(
            name="Test Webhook Rule",
            model=self.manufacturer_ct,
            events=['create'],
            action_type=EventRule.ACTION_WEBHOOK,
            action_config={
                'url': 'https://example.com/webhook-receiver',
                'method': 'POST',
                'secret': 'mysecretkey',
                'headers': {'X-Custom-Header': 'CustomValue'}
            },
            enabled=True
        )

        event = Event.objects.create(
            model=self.manufacturer_ct,
            object_id=101,
            action='create',
            data={'app_label': 'assets', 'model_name': 'manufacturer'},
        )

        # Execute event rule action (under atomic on_commit context)
        with transaction.atomic():
            dispatch_event(Manufacturer, event, 'create')
        
        # Verify webhook request parameters
        self.assertTrue(mock_request.called)
        call_args = mock_request.call_args[1]
        self.assertEqual(call_args['method'], 'POST')
        self.assertEqual(call_args['url'], 'https://example.com/webhook-receiver')
        
        # Verify HMAC signature
        headers = call_args['headers']
        self.assertEqual(headers['X-Custom-Header'], 'CustomValue')
        self.assertIn('X-Hub-Signature-256', headers)
        
        body = call_args['data']
        expected_sig = hmac.new(
            b'mysecretkey',
            body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        self.assertEqual(headers['X-Hub-Signature-256'], f'sha256={expected_sig}')

    @patch('requests.request')
    def test_webhook_delivery_via_linked_endpoint(self, mock_request):
        # A rule linked to a WebhookEndpoint sources URL/method/headers/secret/retry from
        # the endpoint — no url/secret needed in action_config.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        endpoint = WebhookEndpoint.objects.create(
            name="Linked Endpoint",
            url="https://example.com/linked-receiver",
            http_method="POST",
            secret="endpoint-secret",
            headers={'X-From': 'endpoint'},
            retry_count=5,
            retry_backoff=30,
        )
        EventRule.objects.create(
            name="Linked Webhook Rule",
            model=self.manufacturer_ct,
            events=['create'],
            action_type=EventRule.ACTION_WEBHOOK,
            webhook=endpoint,
            action_config={},
            enabled=True,
        )
        event = Event.objects.create(
            model=self.manufacturer_ct,
            object_id=202,
            action='create',
            data={'app_label': 'assets', 'model_name': 'manufacturer'},
        )

        with transaction.atomic():
            dispatch_event(Manufacturer, event, 'create')

        self.assertTrue(mock_request.called)
        call_args = mock_request.call_args[1]
        self.assertEqual(call_args['url'], 'https://example.com/linked-receiver')
        self.assertEqual(call_args['method'], 'POST')

        headers = call_args['headers']
        self.assertEqual(headers['X-From'], 'endpoint')
        body = call_args['data']
        expected_sig = hmac.new(
            endpoint.secret_decrypted.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(headers['X-Hub-Signature-256'], f'sha256={expected_sig}')

    @patch('requests.request')
    def test_disabled_linked_endpoint_suppresses_delivery(self, mock_request):
        endpoint = WebhookEndpoint.objects.create(
            name="Disabled Endpoint",
            url="https://example.com/disabled",
            http_method="POST",
            enabled=False,
        )
        EventRule.objects.create(
            name="Rule With Disabled Endpoint",
            model=self.manufacturer_ct,
            events=['create'],
            action_type=EventRule.ACTION_WEBHOOK,
            webhook=endpoint,
            enabled=True,
        )
        event = Event.objects.create(
            model=self.manufacturer_ct,
            object_id=203,
            action='create',
            data={'app_label': 'assets', 'model_name': 'manufacturer'},
        )

        with transaction.atomic():
            dispatch_event(Manufacturer, event, 'create')

        self.assertFalse(mock_request.called)

    def test_legacy_script_rule_does_not_crash(self):
        # Rows with action_type='script' may exist in the DB from before the action was
        # removed. They must be silently skipped without raising.
        EventRule.objects.filter(pk__gt=0).delete()
        EventRule.objects.create(
            name="Legacy Script Rule",
            model=self.manufacturer_ct,
            events=['update'],
            action_type='script',  # no longer a valid choice, but old rows may exist
            action_config={'script': 'legacy.py'},
            enabled=True,
        )
        mfr = Manufacturer.objects.create(name="LegacyTest", slug="legacy-test-mfr")
        # Must not raise; dispatch_event creates and processes a new Event.
        dispatch_event(Manufacturer, mfr, 'update')
        dispatched = Event.objects.filter(
            model=self.manufacturer_ct, object_id=mfr.pk, action='update'
        ).order_by('-pk').first()
        self.assertIsNotNone(dispatched)
        self.assertTrue(dispatched.processed)

    @patch('requests.post')
    def test_notification_channels(self, mock_post):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        # Slack Channel
        slack_channel = NotificationChannel.objects.create(
            name="Slack Devs",
            channel_type=NotificationChannel.TYPE_SLACK,
            config={'webhook_url': 'https://hooks.slack.com/services/abc'}
        )

        # Teams Channel
        teams_channel = NotificationChannel.objects.create(
            name="Teams Alerts",
            channel_type=NotificationChannel.TYPE_TEAMS,
            config={'webhook_url': 'https://webhook.office.com/webhookb2/xyz'}
        )

        # In-App Channel — needs a staff user to receive the notification
        staff_user = User.objects.create_user(
            username='channel_staff', password='x', is_staff=True, is_active=True
        )
        in_app_channel = NotificationChannel.objects.create(
            name="In-App Feed",
            channel_type=NotificationChannel.TYPE_IN_APP
        )

        # Test Slack sending
        res = send_notification_to_channel(slack_channel, "Subject Slack", "Body Slack")
        self.assertTrue(res)
        self.assertIn("hooks.slack.com", mock_post.call_args[0][0])

        # Test Teams sending
        res = send_notification_to_channel(teams_channel, "Subject Teams", "Body Teams")
        self.assertTrue(res)
        self.assertIn("webhook.office.com", mock_post.call_args[0][0])

        # Test In-App Notification creation — creates one notification per resolved user
        initial_count = Notification.objects.count()
        res = send_notification_to_channel(in_app_channel, "Subject In-App", "Body In-App")
        self.assertTrue(res)
        self.assertGreater(Notification.objects.count(), initial_count)
        notif = Notification.objects.filter(user=staff_user).latest('pk')
        self.assertEqual(notif.subject, "Subject In-App")


class WebhookRetryTestCase(TransactionTestCase):
    """send_webhook_task retry behaviour."""

    BASE_KWARGS = dict(
        url='https://example.com/hook',
        method='POST',
        headers={},
        secret='',
        event_action='create',
        event_model_app_label='assets',
        event_model_name='manufacturer',
        event_object_id=1,
        event_timestamp_iso='2024-01-01T00:00:00+00:00',
        event_data={},
    )

    @patch('core.tasks.webhooks.requests.request')
    @patch('core.tasks.webhooks.async_task')
    def test_5xx_retries(self, mock_async, mock_request):
        from core.tasks.webhooks import send_webhook_task
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__('requests').HTTPError(response=resp)
        mock_request.return_value = resp

        send_webhook_task(**self.BASE_KWARGS, retry_count=2, retry_backoff=0)

        mock_async.assert_called_once()
        _, kw = mock_async.call_args
        self.assertEqual(kw['attempt'], 1)
        self.assertEqual(kw['retry_count'], 2)

    @patch('core.tasks.webhooks.requests.request')
    @patch('core.tasks.webhooks.async_task')
    def test_5xx_gives_up_after_max_attempts(self, mock_async, mock_request):
        from core.tasks.webhooks import send_webhook_task
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__('requests').HTTPError(response=resp)
        mock_request.return_value = resp

        send_webhook_task(**self.BASE_KWARGS, attempt=2, retry_count=2)

        mock_async.assert_not_called()

    @patch('core.tasks.webhooks.requests.request')
    @patch('core.tasks.webhooks.async_task')
    def test_4xx_does_not_retry(self, mock_async, mock_request):
        from core.tasks.webhooks import send_webhook_task
        resp = MagicMock(status_code=422)
        resp.raise_for_status.return_value = None
        mock_request.return_value = resp

        send_webhook_task(**self.BASE_KWARGS, retry_count=3)

        mock_async.assert_not_called()

    @patch('core.tasks.webhooks.requests.request')
    @patch('core.tasks.webhooks.async_task')
    def test_2xx_no_retry(self, mock_async, mock_request):
        from core.tasks.webhooks import send_webhook_task
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        mock_request.return_value = resp

        send_webhook_task(**self.BASE_KWARGS, retry_count=3)

        mock_async.assert_not_called()

    @patch('core.tasks.webhooks.requests.request')
    @patch('core.tasks.webhooks.async_task')
    @patch('core.tasks.webhooks.Schedule')
    def test_5xx_with_backoff_schedules_delayed_retry(self, mock_schedule, mock_async, mock_request):
        """A positive retry_backoff must defer the retry via a one-off Schedule,
        not re-enqueue immediately. The kwargs must round-trip through the same
        ast.literal_eval the django-q2 scheduler uses."""
        import ast
        from core.tasks.webhooks import send_webhook_task
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__('requests').HTTPError(response=resp)
        mock_request.return_value = resp

        send_webhook_task(**self.BASE_KWARGS, retry_count=2, retry_backoff=60)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        _, kw = mock_schedule.objects.create.call_args
        self.assertEqual(kw['func'], 'core.tasks.send_webhook_task')
        self.assertEqual(kw['schedule_type'], mock_schedule.ONCE)
        self.assertGreater(kw['next_run'], timezone.now())
        retry = ast.literal_eval(kw['kwargs'])
        self.assertEqual(retry['attempt'], 1)
        self.assertEqual(retry['retry_count'], 2)
        self.assertEqual(retry['retry_backoff'], 60)
        self.assertEqual(retry['url'], self.BASE_KWARGS['url'])

    @patch('core.tasks.webhooks.requests.request')
    @patch('core.tasks.webhooks.async_task')
    @patch('core.tasks.webhooks.Schedule')
    def test_endpoint_secret_not_persisted_in_retry_schedule(self, mock_schedule, mock_async, mock_request):
        """WS5-4: an endpoint-linked retry must re-derive the secret from the endpoint, never
        write it into Schedule.kwargs (which django-q stores plaintext)."""
        import ast
        from core.tasks.webhooks import send_webhook_task
        endpoint = WebhookEndpoint.objects.create(
            name='WH', url='https://example.com/hook', secret='top-secret',
        )
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__('requests').HTTPError(response=resp)
        mock_request.return_value = resp

        kwargs = dict(self.BASE_KWARGS, secret='', webhook_endpoint_id=endpoint.pk,
                      retry_count=2, retry_backoff=60)
        send_webhook_task(**kwargs)

        # The HMAC was still computed (secret re-derived from the endpoint at run time).
        self.assertIn('X-Hub-Signature-256', mock_request.call_args[1]['headers'])
        # The retry Schedule.kwargs must NOT contain the secret — only the endpoint id.
        mock_schedule.objects.create.assert_called_once()
        _, kw = mock_schedule.objects.create.call_args
        self.assertNotIn('top-secret', kw['kwargs'])
        retry = ast.literal_eval(kw['kwargs'])
        self.assertEqual(retry['webhook_endpoint_id'], endpoint.pk)
        self.assertEqual(retry['secret'], '')
