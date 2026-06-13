import logging
from django.utils import timezone
from django.contrib.auth import get_user_model
from core.models import Notification
from .models import Subscription

logger = logging.getLogger(__name__)
User = get_user_model()


def check_subscription_expiries_and_reminders():
    """
    Daily background task to:
    1. Mark subscriptions that have passed their renewal date as 'expired'.
    2. Send renewal warnings (30, 14, and 7 days prior).
    """
    today = timezone.now().date()
    
    # 1. Handle auto-expiries
    expired_count = 0
    expired_subs = Subscription.objects.filter(
        status='active',
        renewal_date__lt=today
    )
    for sub in expired_subs:
        sub.status = 'expired'
        sub.save(update_fields=['status'])
        expired_count += 1
        
        # Notify owner and admins about auto-expiry
        recipients = set(User.objects.filter(is_staff=True))
        if sub.owner:
            recipients.add(sub.owner)
            
        for user in recipients:
            Notification.objects.create(
                user=user,
                subject=f"Subscription Expired: {sub.name}",
                message=f"The subscription '{sub.name}' from provider '{sub.provider}' has expired as of {sub.renewal_date}.",
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
            status='active',
            renewal_date=target_date
        )
        for sub in subs_to_remind:
            recipients = set(User.objects.filter(is_staff=True))
            if sub.owner:
                recipients.add(sub.owner)
                
            for user in recipients:
                Notification.objects.create(
                    user=user,
                    subject=f"Subscription Renewal Warning: {sub.name} in {days} Days",
                    message=f"The subscription '{sub.name}' from provider '{sub.provider}' is due for renewal on {sub.renewal_date} ({days} days remaining). Cost: {sub.renewal_cost} {sub.currency}.",
                    level=Notification.LEVEL_WARNING,
                    target_url=sub.get_absolute_url()
                )
