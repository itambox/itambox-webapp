import logging
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from core.models import Notification
from core.tasks.context import TaskContext

logger = logging.getLogger(__name__)
User = get_user_model()

def notify_new_request_task(request_id):
    from assets.models import AssetRequest

    # Resolve the request unscoped first to learn its tenant, then run the rest
    # under that tenant's context: the fetch is tenant-scoped, the change-log is
    # attributed correctly, and — critically — recipients are limited to that
    # tenant's staff. Previously this ran with no TaskContext and notified every
    # platform-wide is_staff user, leaking one tenant's requests to all others.
    request = AssetRequest.all_objects.filter(pk=request_id).first()
    if request is None:
        return

    with TaskContext(tenant_id=request.tenant_id, user_id=None):
        instance = AssetRequest.objects.filter(pk=request_id).first()
        if instance is None or instance.parent is not None:
            return

        admins = User.objects.filter(
            is_staff=True, is_active=True, memberships__tenant_id=instance.tenant_id
        ).distinct()
        notifications = [
            Notification(
                user=admin,
                subject=_("New Asset Request from %(requester)s") % {"requester": instance.requester},
                message=_("%(requester)s has requested %(item)s.") % {"requester": instance.requester, "item": instance},
                level=Notification.LEVEL_INFO,
                target_url=instance.get_absolute_url()
            )
            for admin in admins
        ]
        if notifications:
            Notification.objects.bulk_create(notifications)
