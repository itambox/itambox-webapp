import logging
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from core.models import Notification
from core.tasks.context import TaskContext
from .models import Subscription, SubscriptionStatusChoices

logger = logging.getLogger(__name__)
User = get_user_model()


def check_subscription_expiries_and_reminders():
    """
    Daily background task to:
    1. Mark subscriptions that have passed their renewal date as 'expired'.
    2. Send renewal warnings (30, 14, and 7 days prior).

    This task iterates subscriptions across all tenants, so each subscription's
    work is wrapped in its own TaskContext bound to that subscription's tenant.
    That ensures every save and Notification is recorded as an ObjectChange and
    attributed to the correct tenant rather than the global (None) context.
    """
    today = timezone.now().date()

    # 1. Handle auto-expiries
    expired_count = 0
    expired_subs = Subscription.objects.filter(
        status=SubscriptionStatusChoices.ACTIVE,
        renewal_date__lt=today
    )
    for sub in expired_subs:
        with TaskContext(tenant_id=sub.tenant_id, user_id=None):
            sub.status = SubscriptionStatusChoices.EXPIRED
            sub.save(update_fields=['status'])
            expired_count += 1

            # Notify owner and admins about auto-expiry
            # Scope recipients to staff who belong to this subscription's tenant.
            # The bare is_staff=True query notified every platform operator with
            # another tenant's per-tenant financials (a cross-tenant data flow).
            recipients = set(User.objects.filter(
                is_staff=True, is_active=True, memberships__tenant_id=sub.tenant_id
            ).distinct())
            if sub.owner:
                recipients.add(sub.owner)

            for user in recipients:
                Notification.objects.create(
                    user=user,
                    subject=_("Subscription Expired: %(name)s") % {"name": sub.name},
                    message=_(
                        "The subscription '%(name)s' from provider '%(provider)s' "
                        "has expired as of %(date)s."
                    ) % {
                        "name": sub.name,
                        "provider": sub.provider,
                        "date": sub.renewal_date,
                    },
                    level=Notification.LEVEL_WARNING,
                    target_url=sub.get_absolute_url()
                )

    if expired_count:
        logger.info(f"Marked {expired_count} subscriptions as expired.")

    # 2. Handle renewal reminders (30, 14, 7 days warning)
    reminder_days = [30, 14, 7]
    for days in reminder_days:
        target_date = today + timezone.timedelta(days=days)
        subs_to_remind = Subscription.objects.filter(
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=target_date
        )
        for sub in subs_to_remind:
            with TaskContext(tenant_id=sub.tenant_id, user_id=None):
                # Scope recipients to staff who belong to this subscription's tenant.
                # The bare is_staff=True query notified every platform operator with
                # another tenant's per-tenant financials (a cross-tenant data flow).
                recipients = set(User.objects.filter(
                    is_staff=True, is_active=True, memberships__tenant_id=sub.tenant_id
                ).distinct())
                if sub.owner:
                    recipients.add(sub.owner)

                for user in recipients:
                    Notification.objects.create(
                        user=user,
                        subject=_(
                            "Subscription Renewal Warning: %(name)s in %(days)s Days"
                        ) % {"name": sub.name, "days": days},
                        message=_(
                            "The subscription '%(name)s' from provider '%(provider)s' "
                            "is due for renewal on %(date)s (%(days)s days remaining). "
                            "Cost: %(cost)s %(currency)s."
                        ) % {
                            "name": sub.name,
                            "provider": sub.provider,
                            "date": sub.renewal_date,
                            "days": days,
                            "cost": sub.renewal_cost,
                            "currency": sub.currency,
                        },
                        level=Notification.LEVEL_WARNING,
                        target_url=sub.get_absolute_url()
                    )
