import json
import hmac
import hashlib
import logging

import requests
from django.contrib.contenttypes.models import ContentType

from core.models import ChangeLoggingMixin
from extras.models import Event, EventRule, NotificationChannel

logger = logging.getLogger(__name__)


def dispatch_event(sender, instance, action, created=None):
    """Dispatch an event when a ChangeLoggingMixin model is created, updated, or deleted."""

    if not issubclass(sender, ChangeLoggingMixin):
        return

    ct = ContentType.objects.get_for_model(sender)

    event = Event.objects.create(
        model=ct,
        object_id=instance.pk,
        action=action,
        data={'app_label': ct.app_label, 'model_name': ct.model},
    )

    process_event_rules(event)


def process_event_rules(event):
    """Match and execute event rules for the given event."""

    rules = EventRule.objects.filter(
        model=event.model,
        enabled=True,
    )

    if not rules.exists():
        return

    for rule in rules:
        events_list = rule.events or []
        if event.action not in events_list:
            continue

        if not _check_conditions(rule.conditions, event):
            continue

        _execute_event_action(rule, event)

    event.processed = True
    event.save(update_fields=['processed'])


def _check_conditions(conditions, event):
    """Evaluate optional JSON conditions on the event."""

    if not conditions:
        return True

    condition_type = conditions.get('type', 'and')
    rules = conditions.get('rules', [])

    if not rules:
        return True

    results = []
    for rule in rules:
        if 'and' in rule or 'or' in rule:
            results.append(_check_conditions(rule, event))
        else:
            results.append(_evaluate_condition(rule, event))

    if condition_type == 'or':
        return any(results)
    return all(results)


def _evaluate_condition(rule, event):
    """Evaluate a single condition rule against the event."""

    field = rule.get('field')
    op = rule.get('op')
    value = rule.get('value')

    if not field or not op:
        return True

    data = event.data or {}
    actual = data.get(field)

    if op == 'eq':
        return actual == value
    elif op == 'neq':
        return actual != value
    elif op == 'contains':
        return str(value) in str(actual) if actual else False
    elif op == 'in':
        return actual in (value if isinstance(value, list) else [value])
    elif op == 'gt':
        try:
            return float(actual) > float(value)
        except (TypeError, ValueError):
            return False
    elif op == 'lt':
        try:
            return float(actual) < float(value)
        except (TypeError, ValueError):
            return False

    return True


def _execute_event_action(rule, event):
    """Execute the action specified by an event rule."""

    if rule.action_type == EventRule.ACTION_WEBHOOK:
        _send_webhook(rule, event)
    elif rule.action_type == EventRule.ACTION_NOTIFICATION:
        _send_notification(rule, event)
    # 'script' action_type was removed; existing rows are silently skipped.
    # Scripts may return as a proper plugin hook post-1.0.


def _send_webhook(rule, event):
    """Send a webhook request based on the rule's action_config."""

    config = rule.action_config or {}
    url = config.get('url')
    if not url:
        return

    method = config.get('method', 'POST').upper()
    headers = config.get('headers', {})
    secret = config.get('secret', '')

    from django_q.tasks import async_task
    from django.db import transaction
    from django.conf import settings

    if getattr(settings, 'Q_CLUSTER', {}).get('sync', False):
        async_task(
            'core.tasks.send_webhook_task',
            url=url,
            method=method,
            headers=headers,
            secret=secret,
            event_action=event.action,
            event_model_app_label=event.model.app_label,
            event_model_name=event.model.model,
            event_object_id=event.object_id,
            event_timestamp_iso=event.timestamp.isoformat(),
            event_data=event.data,
        )
    else:
        transaction.on_commit(
            lambda: async_task(
                'core.tasks.send_webhook_task',
                url=url,
                method=method,
                headers=headers,
                secret=secret,
                event_action=event.action,
                event_model_app_label=event.model.app_label,
                event_model_name=event.model.model,
                event_object_id=event.object_id,
                event_timestamp_iso=event.timestamp.isoformat(),
                event_data=event.data,
            )
        )


def _send_notification(rule, event):
    """Create an in-app notification based on the rule's action_config."""

    from core.models import Notification

    config = rule.action_config or {}
    level = config.get('level', 'info')
    subject = config.get('subject', f"Event: {event.action} on {event.model.model}")
    body = config.get('body', str(event.data))

    try:
        subject = subject.format(event=event, data=event.data)
        body = body.format(event=event, data=event.data)
    except (KeyError, ValueError):
        pass

    target_url = ''
    try:
        model_class = event.model.model_class()
        if model_class and hasattr(model_class, 'get_absolute_url'):
            instance = model_class.objects.filter(pk=event.object_id).first()
            if instance:
                target_url = instance.get_absolute_url()
    except Exception:
        pass

    Notification.objects.create(
        user=None,
        subject=subject,
        message=body,
        level=level,
        target_url=target_url,
    )


def _send_slack_notification(webhook_url, message_text, title=None):
    payload = {
        'text': message_text,
    }
    if title:
        payload['blocks'] = [
            {
                'type': 'header',
                'text': {'type': 'plain_text', 'text': title}
            },
            {
                'type': 'section',
                'text': {'type': 'mrkdwn', 'text': message_text}
            }
        ]
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Slack notification sent — status %s", response.status_code)
        return True
    except requests.RequestException as e:
        logger.error("Slack notification failed: %s", e)
        return False


def _send_teams_notification(webhook_url, message_text, title=None):
    payload = {
        '@type': 'MessageCard',
        '@context': 'https://schema.org/extensions',
        'summary': title or message_text[:80],
        'themeColor': '0076D7',
        'title': title or 'ITAMbox Notification',
        'text': message_text,
    }
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Teams notification sent — status %s", response.status_code)
        return True
    except requests.RequestException as e:
        logger.error("Teams notification failed: %s", e)
        return False


def send_notification_to_channel(channel, subject, body):
    """Deliver a notification via the given channel.

    Supported channel types: email, in_app, slack, teams.
    Webhooks are NOT an alert-delivery channel; they belong to the EventRule system.
    Returns True on success, False on failure.
    """
    if channel.channel_type == NotificationChannel.TYPE_SLACK:
        return _send_slack_notification(
            webhook_url=channel.config.get('webhook_url', ''),
            message_text=body,
            title=subject,
        )

    elif channel.channel_type == NotificationChannel.TYPE_TEAMS:
        return _send_teams_notification(
            webhook_url=channel.config.get('webhook_url', ''),
            message_text=body,
            title=subject,
        )

    elif channel.channel_type == NotificationChannel.TYPE_EMAIL:
        from django.core.mail import get_connection, EmailMessage
        from core.models import EmailSettings

        email_config = EmailSettings.load()
        if not email_config or not email_config.enabled:
            logger.warning(
                "Email channel '%s': system EmailSettings disabled or not configured.",
                channel.name,
            )
            return False

        recipients = channel.config.get('recipients', [])
        if not recipients:
            logger.warning("Email channel '%s': no recipients configured in config.", channel.name)
            return False

        try:
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=email_config.smtp_host,
                port=email_config.smtp_port,
                username=email_config.smtp_username or '',
                password=email_config.smtp_password_decrypted or '',
                use_tls=email_config.smtp_use_tls,
                fail_silently=False,
            )
            msg = EmailMessage(
                subject=subject,
                body=body,
                from_email=f"{email_config.from_name} <{email_config.from_address}>",
                to=recipients,
                connection=connection,
            )
            msg.send()
            return True
        except Exception as exc:
            logger.error("Email delivery via channel '%s' failed: %s", channel.name, exc)
            return False

    elif channel.channel_type == NotificationChannel.TYPE_IN_APP:
        from core.models import Notification
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Resolve target users: explicit list in config → tenant members → global staff
        user_ids = channel.config.get('recipient_users', [])
        if user_ids:
            users = list(User.objects.filter(pk__in=user_ids, is_active=True))
        elif channel.tenant_id:
            users = list(
                User.objects.filter(
                    asset_holder_profiles__tenant_id=channel.tenant_id,
                    is_active=True,
                ).distinct()
            )
        else:
            users = list(User.objects.filter(is_staff=True, is_active=True))

        if not users:
            logger.warning("In-App channel '%s': no recipients found — notifications not sent.", channel.name)
            return False

        Notification.objects.bulk_create([
            Notification(user=user, subject=subject, message=body)
            for user in users
        ])
        return True

    logger.warning("send_notification_to_channel: unhandled channel type '%s'.", channel.channel_type)
    return False

