import logging
from django.contrib.auth import get_user_model
from core.models import Notification

logger = logging.getLogger(__name__)
User = get_user_model()

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
