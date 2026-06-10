import logging

from django.db import transaction, DatabaseError
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from core.models import ChangeLoggingMixin
from extras.models import Event as EventModel
from core.events import dispatch_event

logger = logging.getLogger(__name__)

_SIGNAL_SKIP_MODELS = frozenset(
    ('Event', 'ObjectChange', 'JournalEntry', 'Notification', 'Bookmark', 'ObjectWatch')
)


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
    if sender.__name__ in _SIGNAL_SKIP_MODELS:
        return

    from core.mixins import SoftDeleteMixin
    is_soft_deleted = isinstance(instance, SoftDeleteMixin) and instance.deleted_at is not None

    action = 'delete' if is_soft_deleted else ('create' if created else 'update')
    try:
        transaction.on_commit(lambda: _safe_dispatch(sender, instance, action, created))
    except Exception:
        _safe_dispatch(sender, instance, action, created)

    # Notify watchers (ObjectWatch subscribers only — bookmarks no longer notify)
    try:
        watch_action = 'deleted' if is_soft_deleted else ('created' if created else 'updated')
        _notify_watchers(sender, instance, watch_action)
    except Exception as e:
        logger.debug("Failed to notify watchers on save: %s", e)



@receiver(post_delete)
def event_on_delete(sender, instance, **kwargs):
    if not issubclass(sender, ChangeLoggingMixin):
        return
    if sender.__name__ in _SIGNAL_SKIP_MODELS:
        return
    try:
        transaction.on_commit(lambda: _safe_dispatch(sender, instance, 'delete'))
    except Exception:
        _safe_dispatch(sender, instance, 'delete')

    # Notify watchers
    try:
        _notify_watchers(sender, instance, 'deleted')
    except Exception as e:
        logger.debug("Failed to notify watchers on delete: %s", e)


def _safe_dispatch(sender, instance, action, created=None):
    from django.db import connection
    if 'extras_event' not in connection.introspection.table_names():
        return
    try:
        dispatch_event(sender, instance, action=action, created=created)
    except DatabaseError:
        logger.debug("Event dispatch skipped (table may not exist yet): %s:%s", sender.__name__, action)
    except Exception as e:
        logger.debug("Event dispatch error for %s:%s: %s", sender.__name__, action, e)


def _notify_watchers(sender, instance, action):
    from django.db import connection
    # Pre-flight table check: avoids aborting the Postgres transaction (unlike catching
    # DatabaseError after the fact, which leaves the connection in "transaction aborted" state).
    if 'extras_objectwatch' not in connection.introspection.table_names():
        return

    from extras.models import ObjectWatch
    from core.models import Notification
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(sender)
    watches = ObjectWatch.objects.filter(model=ct, object_id=instance.pk).select_related('user')

    if not watches.exists():
        return

    target_url = ''
    if action != 'deleted' and hasattr(instance, 'get_absolute_url'):
        try:
            target_url = instance.get_absolute_url()
        except Exception:
            pass

    subject = f"Watched item {action}: {instance}"
    message = f"The item '{instance}' you are watching has been {action}."

    for watch in watches:
        Notification.objects.create(
            user=watch.user,
            subject=subject,
            message=message,
            level='info' if action != 'deleted' else 'warning',
            target_url=target_url
        )


