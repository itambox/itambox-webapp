import logging
from django.contrib.auth import get_user_model
from core.models import Notification

logger = logging.getLogger(__name__)
User = get_user_model()

def check_consumable_stock_task(consumable_id):
    from inventory.models import Consumable
    try:
        consumable = Consumable.objects.get(pk=consumable_id)
    except Consumable.DoesNotExist:
        return

    remaining = consumable.available
    min_qty = consumable.min_qty
    
    if remaining < min_qty:
        if remaining <= 0:
            level = Notification.LEVEL_DANGER
            subject = f"Out of Stock: {consumable.name}"
            message = f"The consumable item '{consumable}' is completely OUT OF STOCK! Remaining count is {remaining} (Safety threshold: {min_qty})."
        else:
            level = Notification.LEVEL_WARNING
            subject = f"Low Stock Warning: {consumable.name}"
            message = f"The consumable item '{consumable}' is running low. Remaining count is {remaining} (Safety threshold: {min_qty})."
        
        existing_admin_ids = set(Notification.objects.filter(
            user__is_staff=True,
            subject=subject,
            is_read=False
        ).values_list('user_id', flat=True))

        admins_to_notify = User.objects.filter(is_staff=True).exclude(id__in=existing_admin_ids)
        
        notifications = [
            Notification(
                user=admin,
                subject=subject,
                message=message,
                level=level,
                target_url=consumable.get_absolute_url()
            )
            for admin in admins_to_notify
        ]
        if notifications:
            Notification.objects.bulk_create(notifications)


def notify_new_request_task(request_id):
    from assets.models import AssetRequest
    try:
        instance = AssetRequest.objects.get(pk=request_id)
    except AssetRequest.DoesNotExist:
        return

    if instance.parent is None:
        admins = User.objects.filter(is_staff=True)
        notifications = [
            Notification(
                user=admin,
                subject=f"New Asset Request from {instance.requester}",
                message=f"{instance.requester} has requested {instance}.",
                level=Notification.LEVEL_INFO,
                target_url=instance.get_absolute_url()
            )
            for admin in admins
        ]
        if notifications:
            Notification.objects.bulk_create(notifications)
