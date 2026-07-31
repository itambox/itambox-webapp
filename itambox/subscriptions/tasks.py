import json
import logging

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Notification
from core.tasks.context import TaskContext

from .models import Subscription, SubscriptionStatusChoices

logger = logging.getLogger(__name__)
User = get_user_model()


def _notify_once(*, user, subject, message, target_url):
    identity = json.dumps(
        [user.pk, str(subject), str(message), Notification.LEVEL_WARNING, target_url],
        separators=(",", ":"),
    )
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [identity])
        Notification.objects.get_or_create(
            user=user,
            subject=subject,
            message=message,
            level=Notification.LEVEL_WARNING,
            target_url=target_url,
        )


def _tenant_recipients(subscription):
    recipients = set(
        User.objects.filter(
            is_staff=True,
            is_active=True,
            memberships__tenant_id=subscription.tenant_id,
        ).distinct()
    )
    if subscription.owner and subscription.owner.is_active:
        recipients.add(subscription.owner)
    return recipients


def _notify_expired(subscription):
    for user in _tenant_recipients(subscription):
        _notify_once(
            user=user,
            subject=_("Subscription Expired: %(name)s") % {"name": subscription.name},
            message=_("The subscription '%(name)s' from provider '%(provider)s' has expired as of %(date)s.")
            % {
                "name": subscription.name,
                "provider": subscription.provider,
                "date": subscription.renewal_date,
            },
            target_url=subscription.get_absolute_url(),
        )


def _notify_renewal_warning(subscription, days):
    for user in _tenant_recipients(subscription):
        _notify_once(
            user=user,
            subject=_("Subscription Renewal Warning: %(name)s in %(days)s Days")
            % {"name": subscription.name, "days": days},
            message=_(
                "The subscription '%(name)s' from provider '%(provider)s' "
                "is due for renewal on %(date)s (%(days)s days remaining). "
                "Cost: %(cost)s %(currency)s."
            )
            % {
                "name": subscription.name,
                "provider": subscription.provider,
                "date": subscription.renewal_date,
                "days": days,
                "cost": subscription.renewal_cost,
                "currency": subscription.currency,
            },
            target_url=subscription.get_absolute_url(),
        )


def check_subscription_expiries_and_reminders():
    """
    Daily background task to:
    1. Mark subscriptions that have passed their renewal date as 'expired'.
    2. Send renewal warnings (30, 14, and 7 days prior).

    This task iterates subscriptions across all tenants, so each subscription's
    work is wrapped in its own TaskContext bound to that subscription's tenant.
    That ensures every save and Notification is recorded as an ObjectChange and
    attributed to the correct tenant rather than the global (None) context.

    Both enumerations deliberately bootstrap through ``Subscription.unscoped``
    (see the manager's declaration): the tenant-scoping default manager cannot
    discover the first row here, because the per-tenant scope is only entered
    afterwards. ``unscoped`` widens the tenant boundary and nothing else —
    soft-deleted subscriptions remain excluded — and every row found is still
    processed inside its own tenant's TaskContext below.
    """
    # Renewal dates are calendar dates in the configured application timezone,
    # not UTC dates. Around local midnight ``timezone.now().date()`` can still
    # be yesterday and leave already-expired subscriptions active for a day.
    today = timezone.localdate()

    # 1. Handle auto-expiries
    expired_count = 0
    expired_subs = Subscription.unscoped.filter(status=SubscriptionStatusChoices.ACTIVE, renewal_date__lt=today)
    for candidate in expired_subs:
        with transaction.atomic():
            sub = (
                Subscription.unscoped.select_for_update()
                .filter(
                    pk=candidate.pk,
                    status=SubscriptionStatusChoices.ACTIVE,
                    renewal_date__lt=today,
                )
                .first()
            )
            if sub is None:
                continue
            with TaskContext(tenant_id=sub.tenant_id, user_id=None):
                sub.expire()
                expired_count += 1
                _notify_expired(sub)

    if expired_count:
        logger.info(f"Marked {expired_count} subscriptions as expired.")

    # 2. Handle renewal reminders (30, 14, 7 days warning)
    reminder_days = [30, 14, 7]
    for days in reminder_days:
        target_date = today + timezone.timedelta(days=days)
        subs_to_remind = Subscription.unscoped.filter(status=SubscriptionStatusChoices.ACTIVE, renewal_date=target_date)
        for candidate in subs_to_remind:
            with transaction.atomic():
                sub = (
                    Subscription.unscoped.select_for_update()
                    .filter(
                        pk=candidate.pk,
                        status=SubscriptionStatusChoices.ACTIVE,
                        renewal_date=target_date,
                    )
                    .first()
                )
                if sub is None:
                    continue
                with TaskContext(tenant_id=sub.tenant_id, user_id=None):
                    _notify_renewal_warning(sub, days)
