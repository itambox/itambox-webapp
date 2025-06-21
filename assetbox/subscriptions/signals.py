from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Subscription


@receiver(post_save, sender=Subscription)
def subscription_status_check(sender, instance, **kwargs):
    """Auto-update status if renewal date has passed."""
    if instance.status == 'active' and instance.renewal_date:
        if instance.renewal_date < timezone.now().date():
            instance.status = 'expired'
            instance.save(update_fields=['status'])
