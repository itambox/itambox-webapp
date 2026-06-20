import json
import hmac
import hashlib
import logging

import requests
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from core.models import ChangeLoggingMixin
from extras.models import Event, EventRule, NotificationChannel, WebhookEndpoint

logger = logging.getLogger(__name__)


def _resolve_instance_tenant_id(instance):
    """Resolve the tenant that owns ``instance`` so event rules are matched against the
    object's OWN tenant rather than the ambient tenant contextvar.

    The contextvar is unset in system contexts (management commands, the django-q worker
    after a ``TaskContext`` exits, the shell). There the tenant-scoping manager fails *open*
    (``filter_by_tenant`` returns the unscoped queryset), so matching rules by the contextvar
    would fire EVERY tenant's rules for the object's ContentType — a cross-tenant dispatch
    (foreign webhooks/notifications about another tenant's object). Resolving the tenant from
    the instance itself closes that regardless of context. Returns the tenant pk, or ``None``
    for a tenant-less/global object (in which case only global ``tenant=None`` rules fire).
    """
    tenant_id = getattr(instance, 'tenant_id', None)
    if tenant_id is not None:
        return tenant_id
    # Models that derive their tenant through a relation (assignments/stock) declare a
    # ``tenant_lookup`` ORM path (e.g. 'asset__tenant'); walk it to the owning tenant.
    lookup = getattr(type(instance), 'tenant_lookup', None)
    if lookup:
        obj = instance
        for part in lookup.split('__'):
            obj = getattr(obj, part, None)
            if obj is None:
                return None
        return getattr(obj, 'pk', None)
    return None


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

    process_event_rules(event, _resolve_instance_tenant_id(instance))


def process_event_rules(event, instance_tenant_id=None):
    """Match and execute event rules for the given event.

    Rules are scoped to the triggering object's OWN tenant (plus global ``tenant=None``
    rules), read through the unscoped ``_base_manager`` so the result NEVER depends on the
    ambient tenant contextvar (which fails open in system contexts). See
    ``_resolve_instance_tenant_id``.
    """
    from django.db.models import Q

    rules = EventRule._base_manager.filter(
        model=event.model,
        enabled=True,
        deleted_at__isnull=True,
    ).filter(
        Q(tenant_id=instance_tenant_id) | Q(tenant__isnull=True)
    ).select_related('webhook')

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
        # A nested group has the same shape as the top level ({'type', 'rules'});
        # a leaf condition has 'field'/'op'. Detect groups by the 'rules' key.
        if isinstance(rule, dict) and 'rules' in rule:
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
    """Send a webhook request for the rule.

    Prefers the linked WebhookEndpoint (``rule.webhook``) — its URL, method, headers,
    decrypted secret and retry policy. Falls back to the legacy ``action_config`` JSON
    (``url``/``method``/``headers``/``secret``) for rules created before endpoints could
    be linked; in that case retry settings are borrowed from an endpoint registered for
    the same URL, if any.
    """

    config = rule.action_config or {}
    endpoint = rule.webhook

    if endpoint is not None:
        if not endpoint.enabled:
            return
        url = endpoint.url
        method = endpoint.http_method
        headers = endpoint.headers or {}
        secret = endpoint.secret_decrypted
        retry_count = endpoint.retry_count
        retry_backoff = endpoint.retry_backoff
        # Allow header overrides from action_config without leaking the secret into JSON.
        headers = {**headers, **(config.get('headers') or {})}
    else:
        url = config.get('url')
        if not url:
            return
        method = config.get('method', 'POST')
        headers = config.get('headers', {})
        secret = config.get('secret', '')
        match = WebhookEndpoint.objects.filter(url=url, enabled=True).first()
        retry_count = match.retry_count if match else 3
        retry_backoff = match.retry_backoff if match else 60

    if not url:
        return
    method = (method or 'POST').upper()

    from django_q.tasks import async_task
    from django.db import transaction
    from django.conf import settings

    task_kwargs = dict(
        url=url, method=method, headers=headers, secret=secret,
        event_action=event.action,
        event_model_app_label=event.model.app_label,
        event_model_name=event.model.model,
        event_object_id=event.object_id,
        event_timestamp_iso=event.timestamp.isoformat(),
        event_data=event.data,
        retry_count=retry_count,
        retry_backoff=retry_backoff,
    )

    if getattr(settings, 'Q_CLUSTER', {}).get('sync', False):
        async_task('core.tasks.send_webhook_task', **task_kwargs)
    else:
        transaction.on_commit(lambda: async_task('core.tasks.send_webhook_task', **task_kwargs))


def _render_template(template, event):
    """Render an admin-supplied notification template.

    Preserves the historical ``{event.action}`` / ``{event.model.model}`` /
    ``{data[...]}`` placeholder syntax, but binds ``event`` to a sanitized
    namespace of plain scalars instead of the live ORM instance. ``str.format``
    permits attribute/index traversal of its arguments (e.g.
    ``{event.save.__func__.__globals__[...]}``), so handing it the ORM object is
    an information-disclosure vector for anyone who can edit a rule's
    ``action_config``. A nested SimpleNamespace of strings has no such gadget.
    """
    from types import SimpleNamespace

    if not template:
        return template

    safe_event = SimpleNamespace(
        action=str(event.action),
        object_id=str(event.object_id),
        model=SimpleNamespace(
            model=str(event.model.model),
            app_label=str(event.model.app_label),
        ),
        data=event.data,
    )
    try:
        return template.format(event=safe_event, data=event.data)
    except (KeyError, ValueError, IndexError, AttributeError):
        return template


def _send_notification(rule, event):
    """Create an in-app notification based on the rule's action_config."""

    from core.models import Notification

    config = rule.action_config or {}
    level = config.get('level', 'info')
    subject = config.get('subject', _("Event: %(action)s on %(model)s") % {
        'action': event.action, 'model': event.model.model,
    })
    body = config.get('body', str(event.data))

    # Render against a sanitized namespace (see _render_template) so an
    # attacker-editable action_config can't traverse a live ORM object.
    subject = _render_template(subject, event)
    body = _render_template(body, event)

    target_url = ''
    try:
        model_class = event.model.model_class()
        if model_class and hasattr(model_class, 'get_absolute_url'):
            instance = model_class.objects.filter(pk=event.object_id).first()
            if instance:
                target_url = instance.get_absolute_url()
    except Exception:
        pass

    if rule.tenant_id:
        # A tenant-scoped rule must fan out to the rule's tenant members, NOT create a global
        # user=None row that any authenticated user could open by pk (cross-tenant leak of the
        # rule's subject/body + the target object's URL). Mirrors the IN_APP channel branch.
        from django.contrib.auth import get_user_model
        User = get_user_model()
        users = User.objects.filter(
            memberships__tenant_id=rule.tenant_id, is_active=True
        ).distinct()
        Notification.objects.bulk_create([
            Notification(user=u, subject=subject, message=body, level=level, target_url=target_url)
            for u in users
        ])
    else:
        # Truly system-wide (tenant=None) rule may broadcast.
        Notification.objects.create(
            user=None, subject=subject, message=body, level=level, target_url=target_url,
        )


def _is_safe_outbound_url(url):
    """SSRF guard shared by the synchronous notification senders."""
    from django.core.exceptions import ValidationError
    from core.validators import validate_external_url
    try:
        validate_external_url(url)
        return True
    except ValidationError as exc:
        logger.error("Outbound notification to %s blocked by SSRF guard: %s", url, exc)
        return False


def _send_slack_notification(webhook_url, message_text, title=None):
    if not _is_safe_outbound_url(webhook_url):
        return False
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
    if not _is_safe_outbound_url(webhook_url):
        return False
    payload = {
        '@type': 'MessageCard',
        '@context': 'https://schema.org/extensions',
        'summary': title or message_text[:80],
        'themeColor': '0076D7',
        'title': title or str(_('ITAMbox Notification')),
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
            # Members of the channel's tenant (via TenantMembership) — covers
            # users with no AssetHolder profile, unlike the old
            # asset_holder_profiles join.
            users = list(
                User.objects.filter(
                    memberships__tenant_id=channel.tenant_id,
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

