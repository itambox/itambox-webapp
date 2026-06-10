import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from django.test import TransactionTestCase
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.db import transaction

from core.models import Job, Notification
from extras.models import NotificationChannel
from extras.models import Event, EventRule
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

    def test_script_job_action(self):
        rule = EventRule.objects.create(
            name="Test Script Rule",
            model=self.manufacturer_ct,
            events=['update'],
            action_type=EventRule.ACTION_SCRIPT,
            action_config={'script': 'my_custom_script.py'},
            enabled=True
        )

        event = Event.objects.create(
            model=self.manufacturer_ct,
            object_id=202,
            action='update',
            data={'app_label': 'assets', 'model_name': 'manufacturer'},
        )

        dispatch_event(Manufacturer, event, 'update')

        # Check job created
        job = Job.objects.filter(name="Script: my_custom_script.py").first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, Job.STATUS_PENDING)
        self.assertEqual(job.data['event_action'], 'update')

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
