"""Tests for NotificationChannelForm typed-config assembly.

The form exposes friendly per-type fields (webhook URL, recipient emails,
recipient users) and assembles them into the model's single `config` JSON.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from core.forms import NotificationChannelForm
from core.models import NotificationChannel
from itambox.middleware import _current_user

User = get_user_model()


class NotificationChannelFormTests(TestCase):
    def setUp(self):
        super().setUp()
        # Run as a superuser so the tenant field stays and the user queryset
        # is unrestricted (keeps these unit tests free of tenant-profile setup).
        self.admin = User.objects.create_user(
            username='form_admin', password='x', is_superuser=True, is_staff=True
        )
        _current_user.set(self.admin)

    def tearDown(self):
        _current_user.set(None)
        super().tearDown()

    def test_slack_webhook_url_assembled_into_config(self):
        form = NotificationChannelForm(data={
            'name': 'Ops Slack',
            'channel_type': NotificationChannel.TYPE_SLACK,
            'webhook_url': 'https://hooks.slack.com/services/abc',
            'enabled': True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        channel = form.save()
        self.assertEqual(channel.config, {'webhook_url': 'https://hooks.slack.com/services/abc'})

    def test_slack_requires_webhook_url(self):
        form = NotificationChannelForm(data={
            'name': 'Ops Slack',
            'channel_type': NotificationChannel.TYPE_SLACK,
            'webhook_url': '',
            'enabled': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('webhook_url', form.errors)

    def test_email_recipients_parsed_into_list(self):
        form = NotificationChannelForm(data={
            'name': 'IT Mailbox',
            'channel_type': NotificationChannel.TYPE_EMAIL,
            'email_recipients': 'alice@example.com, bob@example.com\ncarol@example.com',
            'enabled': True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        channel = form.save()
        self.assertEqual(
            channel.config,
            {'recipients': ['alice@example.com', 'bob@example.com', 'carol@example.com']},
        )

    def test_email_rejects_invalid_address(self):
        form = NotificationChannelForm(data={
            'name': 'IT Mailbox',
            'channel_type': NotificationChannel.TYPE_EMAIL,
            'email_recipients': 'alice@example.com, not-an-email',
            'enabled': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email_recipients', form.errors)

    def test_email_requires_at_least_one_recipient(self):
        form = NotificationChannelForm(data={
            'name': 'IT Mailbox',
            'channel_type': NotificationChannel.TYPE_EMAIL,
            'email_recipients': '',
            'enabled': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email_recipients', form.errors)

    def test_in_app_empty_recipients_is_valid(self):
        form = NotificationChannelForm(data={
            'name': 'In-App Feed',
            'channel_type': NotificationChannel.TYPE_IN_APP,
            'enabled': True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        channel = form.save()
        self.assertEqual(channel.config, {})

    def test_in_app_specific_users_assembled(self):
        target = User.objects.create_user(username='recipient', password='x', is_active=True)
        form = NotificationChannelForm(data={
            'name': 'In-App Feed',
            'channel_type': NotificationChannel.TYPE_IN_APP,
            'in_app_recipient_users': [target.pk],
            'enabled': True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        channel = form.save()
        self.assertEqual(channel.config, {'recipient_users': [target.pk]})

    def test_existing_config_prefills_typed_fields(self):
        channel = NotificationChannel.objects.create(
            name='Existing Slack',
            channel_type=NotificationChannel.TYPE_SLACK,
            config={'webhook_url': 'https://hooks.slack.com/services/xyz'},
        )
        form = NotificationChannelForm(instance=channel)
        self.assertEqual(form.initial.get('webhook_url'), 'https://hooks.slack.com/services/xyz')

    def test_existing_email_config_prefills_newline_joined(self):
        channel = NotificationChannel.objects.create(
            name='Existing Email',
            channel_type=NotificationChannel.TYPE_EMAIL,
            config={'recipients': ['a@example.com', 'b@example.com']},
        )
        form = NotificationChannelForm(instance=channel)
        self.assertEqual(form.initial.get('email_recipients'), 'a@example.com\nb@example.com')
