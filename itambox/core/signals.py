import logging

from django.db import transaction, DatabaseError
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from core.mixins import SoftDeleteMixin
from core.models import ChangeLoggingMixin
from core.events import dispatch_event

logger = logging.getLogger(__name__)

_SIGNAL_SKIP_MODELS = frozenset(
    ('Event', 'ObjectChange', 'JournalEntry', 'Notification', 'Bookmark', 'ObjectWatch')
)

# Sentinel distinguishing "no prior row was looked up" (create, or a non-soft-delete
# model) from a prior row whose deleted_at was genuinely None.
_NO_PRIOR = object()

# Tables confirmed to exist, cached for the lifetime of the process.  Positive only:
# a table that exists will always exist; a missing table is re-checked each time.
_TABLES_CONFIRMED: set = set()


def _table_exists(name: str) -> bool:
    if name in _TABLES_CONFIRMED:
        return True
    from django.db import connection
    if name in connection.introspection.table_names():
        _TABLES_CONFIRMED.add(name)
        return True
    return False


@receiver(pre_save)
def validate_custom_validators_on_save(sender, instance, **kwargs):
    if not issubclass(sender, ChangeLoggingMixin):
        return
    try:
        instance.clean()
    except DatabaseError:
        logger.debug("Custom validator skipped (table may not exist yet): %s", sender.__name__)


@receiver(pre_save)
def capture_prior_soft_delete_state(sender, instance, **kwargs):
    """Stash the DB's prior ``deleted_at`` so ``event_on_save`` can detect a transition.

    Read through ``_base_manager`` (unscoped): the prior row may be hidden from the
    default manager by the active tenant context or the soft-delete filter, but the
    transition delete -> set / set -> None still has to be observed precisely.
    """
    if kwargs.get('raw'):
        return
    if not isinstance(instance, SoftDeleteMixin):
        return
    if instance._state.adding or instance.pk is None:
        instance._presave_deleted_at = _NO_PRIOR
        return
    instance._presave_deleted_at = (
        sender._base_manager.filter(pk=instance.pk)
        .values_list('deleted_at', flat=True)
        .first()
    )


def _resolve_save_action(instance, created):
    """Map a save to ('create' | 'update' | 'delete' | 'restore'), watch-verb pair.

    A soft-delete is precise: it fires 'delete' only on the None -> set transition,
    a distinct 'restore' on the set -> None transition (so a restore does not
    masquerade as an 'update'; 'restore' is a declared Event.ACTION_CHOICES value so
    EventRules can subscribe to it), and a plain 'update' when editing an
    already-archived row (no deleted_at transition) so 'delete' is not re-emitted.
    """
    if created:
        return 'create', 'created'

    if not isinstance(instance, SoftDeleteMixin):
        return 'update', 'updated'

    prior = getattr(instance, '_presave_deleted_at', _NO_PRIOR)
    now_deleted = instance.deleted_at is not None

    # No prior row was resolved (e.g. an out-of-band save); fall back to the current
    # state so an archived row still reports 'delete' rather than masquerading as an edit.
    if prior is _NO_PRIOR:
        return ('delete', 'deleted') if now_deleted else ('update', 'updated')

    was_deleted = prior is not None
    if now_deleted and not was_deleted:
        return 'delete', 'deleted'
    if was_deleted and not now_deleted:
        return 'restore', 'restored'
    # set -> set (archived-row edit) or None -> None: an ordinary update.
    return 'update', 'updated'


@receiver(post_save)
def event_on_save(sender, instance, created, **kwargs):
    if kwargs.get('raw'):
        return
    if not issubclass(sender, ChangeLoggingMixin):
        return
    if sender.__name__ in _SIGNAL_SKIP_MODELS:
        return

    action, watch_action = _resolve_save_action(instance, created)
    try:
        transaction.on_commit(lambda: _safe_dispatch(sender, instance, action, created))
    except Exception:
        _safe_dispatch(sender, instance, action, created)

    # Notify watchers (ObjectWatch subscribers only — bookmarks no longer notify).
    # Deferred to on_commit so a rolled-back save spends no has_perm work and writes
    # no Notification rows.
    _defer_notify_watchers(sender, instance, watch_action)


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

    # Notify watchers (deferred to on_commit — see event_on_save).
    _defer_notify_watchers(sender, instance, 'deleted')


def _defer_notify_watchers(sender, instance, action):
    """Schedule watcher notification for after the triggering transaction commits."""
    try:
        transaction.on_commit(lambda: _notify_watchers(sender, instance, action))
    except Exception:
        # No active transaction (autocommit): run inline so the notification still fires.
        try:
            _notify_watchers(sender, instance, action)
        except Exception as e:
            logger.debug("Failed to notify watchers: %s", e)


def _safe_dispatch(sender, instance, action, created=None):
    if not _table_exists('extras_event'):
        return
    try:
        dispatch_event(sender, instance, action=action, created=created)
    except DatabaseError:
        logger.debug("Event dispatch skipped (table may not exist yet): %s:%s", sender.__name__, action)
    except Exception as e:
        logger.debug("Event dispatch error for %s:%s: %s", sender.__name__, action, e)


def _notify_watchers(sender, instance, action):
    # Pre-flight table check: avoids aborting the Postgres transaction (unlike catching
    # DatabaseError after the fact, which leaves the connection in "transaction aborted" state).
    if not _table_exists('extras_objectwatch'):
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

    subject = _("Watched item %(action)s: %(instance)s") % {'action': action, 'instance': instance}
    message = _("The item '%(instance)s' you are watching has been %(action)s.") % {
        'instance': instance, 'action': action,
    }
    level = 'warning' if action == 'deleted' else 'info'

    app_label = ct.app_label
    model_name = ct.model
    perm = f'{app_label}.view_{model_name}'

    notifications = []
    for watch in watches:
        # Re-verify the watcher still has view access before sending — watches outlive
        # tenant-membership removal, so skip silently if access was revoked.
        if not watch.user.has_perm(perm, instance):
            continue
        notifications.append(Notification(
            user=watch.user,
            subject=subject,
            message=message,
            level=level,
            target_url=target_url,
        ))

    if notifications:
        # One INSERT for the surviving watchers instead of O(watchers) round-trips.
        Notification.objects.bulk_create(notifications)
