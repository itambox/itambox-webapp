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
    if sender.__name__ in ('Event', 'ObjectChange', 'JournalEntry', 'Notification'):
        return
    try:
        transaction.on_commit(lambda: _safe_dispatch(sender, instance, 'create' if created else 'update', created))
    except Exception:
        _safe_dispatch(sender, instance, 'create' if created else 'update', created)


@receiver(post_delete)
def event_on_delete(sender, instance, **kwargs):
    if not issubclass(sender, ChangeLoggingMixin):
        return
    if sender.__name__ in ('Event', 'ObjectChange', 'JournalEntry', 'Notification'):
        return
    try:
        transaction.on_commit(lambda: _safe_dispatch(sender, instance, 'delete'))
    except Exception:
        _safe_dispatch(sender, instance, 'delete')


def _safe_dispatch(sender, instance, action, created=None):
    try:
        dispatch_event(sender, instance, action=action, created=created)
    except DatabaseError:
        logger.debug("Event dispatch skipped (table may not exist yet): %s:%s", sender.__name__, action)
    except Exception as e:
        logger.debug("Event dispatch error for %s:%s: %s", sender.__name__, action, e)
