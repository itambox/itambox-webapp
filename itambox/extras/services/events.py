"""Event and EventRule orchestration owned by the extras domain.

Everything that constructs or queries ``Event``, ``EventRule``,
``WebhookDelivery``, ``WebhookEndpoint`` or the condition helpers lives here.
The reusable delivery contracts and transports stay in ``core.events``.
"""

import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_q.tasks import async_task

from core.context import get_current_request_id, get_current_user
from core.models import ChangeLoggingMixin, Notification
from extras.models import Event, EventRule, WebhookDelivery, has_authored_conditions
from extras.tasks.webhooks import WEBHOOK_TASK_PATH, WebhookDeliveryAssertions

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
    tenant_id = getattr(instance, "tenant_id", None)
    if tenant_id is not None:
        return tenant_id
    # Models that derive their tenant through a relation (assignments/stock) declare a
    # ``tenant_lookup`` ORM path (e.g. 'asset__tenant'); walk it to the owning tenant.
    # Fall back to ``changelog_tenant_lookup`` (used by models such as AssetAudit that
    # only declare the changelog attribute) so their events match tenant EventRules.
    lookup = getattr(type(instance), "tenant_lookup", None) or getattr(type(instance), "changelog_tenant_lookup", None)
    if lookup:
        obj = instance
        for part in lookup.split("__"):
            obj = getattr(obj, part, None)
            if obj is None:
                return None
        return getattr(obj, "pk", None)
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
        data={"app_label": ct.app_label, "model_name": ct.model},
    )

    process_event_rules(event, _resolve_instance_tenant_id(instance))


def _eligible_rules(event, instance_tenant_id):
    """Rules that may act on this event: enabled, live, tenant-or-global, subscribed.

    Rules are scoped to the triggering object's OWN tenant (plus global ``tenant=None``
    rules), read through the unscoped ``_base_manager`` so the result NEVER depends on the
    ambient tenant contextvar (which fails open in system contexts). See
    ``_resolve_instance_tenant_id``.
    """
    rules = (
        EventRule._base_manager.filter(
            model=event.model,
            enabled=True,
            deleted_at__isnull=True,
        )
        .filter(Q(tenant_id=instance_tenant_id) | Q(tenant__isnull=True))
        .select_related("webhook")
    )

    for rule in rules:
        if event.action not in (rule.events or []):
            continue

        if rule.conditions_withdrawn:
            logger.info(
                "Skipping event rule pk=%s tenant=%s: conditions withdrawn for 1.0",
                rule.pk,
                rule.tenant_id,
            )
            continue

        if not _check_conditions(rule.conditions, event):
            continue

        yield rule


def process_event_rules(event, instance_tenant_id=None):
    """Attempt every eligible rule exactly once, then mark the event terminal.

    Each eligible rule gets one attempt; a rule action that raises is caught,
    reported with identifiers only, and is terminal for that rule on this event.
    Once every eligible rule has reached a terminal attempt — including the case
    where none was eligible — the event is marked processed so a caught failure
    can never be replayed into duplicate deliveries or notifications.
    """
    if event.processed:
        return

    for rule in _eligible_rules(event, instance_tenant_id):
        # Per-rule isolation: one rule's action raising must not abort the remaining
        # rules for this event. Only identifiers and the exception class may be logged
        # on this boundary — action config, event data, endpoints and secrets must not.
        try:
            _execute_event_action(rule, event, instance_tenant_id)
        # broad except: task-isolation: one failing rule must not prevent other eligible rules
        except Exception as error:
            logger.error(
                "operation=events.rule_action disposition=terminal event_id=%s rule_id=%s error_class=%s",
                event.pk,
                rule.pk,
                type(error).__name__,
            )

    event.processed = True
    event.save(update_fields=["processed"])


def _check_conditions(conditions, event):
    """Fail-closed evaluation of optional JSON conditions on the event.

    WP-15 (D4): the condition feature is withdrawn for 1.0. Any authored
    condition expression (or an unexpected, non-dict payload) fails closed so
    the rule never matches on the near-empty v1 event envelope. Truly empty
    condition payloads (``None``, ``{}``, or a dict whose ``rules`` list is
    empty) preserve the historical unconditional-match behavior.
    """

    if conditions is None or conditions == {}:
        return True
    if not isinstance(conditions, dict):
        return False
    return not has_authored_conditions(conditions)


def _evaluate_condition(rule, event):
    """Evaluate a single condition rule against the event."""

    if not isinstance(rule, dict):
        return False

    field = rule.get("field")
    op = rule.get("op")
    value = rule.get("value")

    if not field or not op:
        return False

    data = event.data or {}
    actual = data.get(field)

    if op == "eq":
        return actual == value
    elif op == "neq":
        return actual != value
    elif op == "contains":
        return str(value) in str(actual) if actual else False
    elif op == "in":
        return actual in (value if isinstance(value, list) else [value])
    elif op in ("gt", "lt"):
        try:
            lhs = float(actual)
            rhs = float(value)
        except (TypeError, ValueError):
            return False
        return lhs > rhs if op == "gt" else lhs < rhs

    return False


def _execute_event_action(rule, event, instance_tenant_id=None):
    """Execute the action specified by an event rule."""

    if rule.action_type == EventRule.ACTION_WEBHOOK:
        _send_webhook(rule, event, instance_tenant_id)
    elif rule.action_type == EventRule.ACTION_NOTIFICATION:
        _send_notification(rule, event)
    # 'script' action_type was removed; existing rows are silently skipped.
    # Scripts may return as a proper plugin hook post-1.0.


def _send_webhook(rule, event, instance_tenant_id=None):
    """Create the durable delivery row for the rule and enqueue its dispatch.

    Prefers the linked WebhookEndpoint (``rule.webhook``) — its URL, method, headers,
    decrypted secret and retry policy. Falls back to the legacy ``action_config`` JSON
    (``url``/``method``/``headers``/``secret``) for rules created before endpoints could
    be linked.

    The task receives identity assertions only: the durable row it names is the sole
    authority for endpoint, event, tenant and test-send state, so neither the endpoint
    secret nor the payload ever enters a django-q package or a retry ``Schedule``.
    """

    config = rule.action_config or {}
    endpoint = rule.webhook

    if endpoint is not None:
        if not endpoint.enabled:
            return
        url = endpoint.url
    else:
        url = config.get("url")

    if not url:
        return

    delivery = WebhookDelivery._base_manager.create(
        tenant_id=instance_tenant_id,
        endpoint=endpoint,
        event=event,
        delivery_id=str(uuid4()),
        status=WebhookDelivery.STATUS_PENDING,
    )
    _enqueue_delivery(delivery)


def _delivery_assertions(delivery):
    """Package the durable identity of ``delivery`` for the worker."""
    return WebhookDeliveryAssertions(
        delivery_pk=delivery.pk,
        delivery_id=UUID(str(delivery.delivery_id)),
        webhook_endpoint_id=delivery.endpoint_id,
        event_id=delivery.event_id,
        tenant_id=delivery.tenant_id,
        test_send=delivery.test_send,
    )


def _enqueue_delivery(delivery, *, actor_id=None, request_id=None):
    """Enqueue the webhook worker for an already-persisted delivery row."""
    if actor_id is None:
        actor_id = getattr(get_current_user(), "pk", None)
    if request_id is None:
        request_id = str(get_current_request_id()) if get_current_request_id() is not None else None
    assertions = _delivery_assertions(delivery)
    task_kwargs = {"actor_id": actor_id, "request_id": request_id}
    if getattr(settings, "Q_CLUSTER", {}).get("sync", False):
        async_task(WEBHOOK_TASK_PATH, assertions, **task_kwargs)
    else:
        transaction.on_commit(lambda: async_task(WEBHOOK_TASK_PATH, assertions, **task_kwargs))


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

    config = rule.action_config or {}
    level = config.get("level", "info")
    subject = config.get(
        "subject",
        _("Event: %(action)s on %(model)s")
        % {
            "action": event.action,
            "model": event.model.model,
        },
    )
    body = config.get("body", str(event.data))

    # Render against a sanitized namespace (see _render_template) so an
    # attacker-editable action_config can't traverse a live ORM object.
    subject = _render_template(subject, event)
    body = _render_template(body, event)

    target_url = ""
    try:
        model_class = event.model.model_class()
        if model_class and hasattr(model_class, "get_absolute_url"):
            instance = model_class.objects.filter(pk=event.object_id).first()
            if instance:
                target_url = instance.get_absolute_url()
    except Exception:
        pass

    if rule.tenant_id:
        # A tenant-scoped rule must fan out to the rule's tenant members, NOT create a global
        # user=None row that any authenticated user could open by pk (cross-tenant leak of the
        # rule's subject/body + the target object's URL). Mirrors the IN_APP channel branch.
        User = get_user_model()
        users = User.objects.filter(memberships__tenant_id=rule.tenant_id, is_active=True).distinct()
        Notification.objects.bulk_create(
            [Notification(user=u, subject=subject, message=body, level=level, target_url=target_url) for u in users]
        )
    else:
        # Truly system-wide (tenant=None) rule may broadcast.
        Notification.objects.create(
            user=None,
            subject=subject,
            message=body,
            level=level,
            target_url=target_url,
        )
