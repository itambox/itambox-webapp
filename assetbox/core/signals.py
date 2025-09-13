import logging

from django.db import transaction, DatabaseError
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from core.models import ChangeLoggingMixin, Event as EventModel
from core.events import dispatch_event

logger = logging.getLogger(__name__)


@receiver(pre_save)
def validate_custom_validators_on_save(sender, instance, **kwargs):
    if not issubclass(sender, ChangeLoggingMixin):
        return
    try:
        instance.clean()
    except DatabaseError:
        logger.debug("Custom validator skipped (table may not exist yet): %s", sender.__name__)




@receiver(post_save)
def event_on_save(sender, instance, created, **kwargs):
    if not issubclass(sender, ChangeLoggingMixin):
        return
    if sender.__name__ in ('Event', 'ObjectChange', 'JournalEntry', 'Notification', 'Bookmark'):
        return
    try:
        transaction.on_commit(lambda: _safe_dispatch(sender, instance, 'create' if created else 'update', created))
    except Exception:
        _safe_dispatch(sender, instance, 'create' if created else 'update', created)
    
    # Notify bookmark subscribers
    try:
        _notify_bookmark_subscribers(sender, instance, 'created' if created else 'updated')
    except Exception as e:
        logger.debug("Failed to notify bookmark subscribers on save: %s", e)


@receiver(post_delete)
def event_on_delete(sender, instance, **kwargs):
    if not issubclass(sender, ChangeLoggingMixin):
        return
    if sender.__name__ in ('Event', 'ObjectChange', 'JournalEntry', 'Notification', 'Bookmark'):
        return
    try:
        transaction.on_commit(lambda: _safe_dispatch(sender, instance, 'delete'))
    except Exception:
        _safe_dispatch(sender, instance, 'delete')

    # Notify bookmark subscribers
    try:
        _notify_bookmark_subscribers(sender, instance, 'deleted')
    except Exception as e:
        logger.debug("Failed to notify bookmark subscribers on delete: %s", e)


def _safe_dispatch(sender, instance, action, created=None):
    try:
        dispatch_event(sender, instance, action=action, created=created)
    except DatabaseError:
        logger.debug("Event dispatch skipped (table may not exist yet): %s:%s", sender.__name__, action)
    except Exception as e:
        logger.debug("Event dispatch error for %s:%s: %s", sender.__name__, action, e)


def _notify_bookmark_subscribers(sender, instance, action):
    from core.models import Bookmark, Notification
    from django.contrib.contenttypes.models import ContentType
    
    ct = ContentType.objects.get_for_model(sender)
    bookmarks = Bookmark.objects.filter(model=ct, object_id=instance.pk).select_related('user')
    
    if not bookmarks.exists():
        return
        
    subject = f"Bookmarked Item {action.title()}: {instance}"
    message = f"The item '{instance}' you bookmarked has been {action}."
    
    target_url = None
    if action != 'deleted' and hasattr(instance, 'get_absolute_url'):
        try:
            target_url = instance.get_absolute_url()
        except Exception:
            pass
            
    for bookmark in bookmarks:
        Notification.objects.create(
            user=bookmark.user,
            subject=subject,
            message=message,
            level='info' if action != 'deleted' else 'warning',
            target_url=target_url
        )

