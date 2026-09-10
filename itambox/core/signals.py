import logging

from django.core.exceptions import FieldDoesNotExist
from django.db import DatabaseError
from django.db.models.signals import pre_save
from django.dispatch import receiver

from core.mixins import suppress_custom_field_data_validation
from core.models import ChangeLoggingMixin

logger = logging.getLogger(__name__)


def _clean_without_custom_field_validation(instance):
    with suppress_custom_field_data_validation(instance):
        instance.clean()


@receiver(pre_save)
def validate_custom_validators_on_save(sender, instance, update_fields=None, **kwargs):
    if not issubclass(sender, ChangeLoggingMixin):
        return
    try:
        dependencies = frozenset(getattr(sender, "custom_field_data_validation_dependencies", frozenset()))
        # Django accepts both the field name and the ``attname`` spelling in
        # ``update_fields`` without normalising them, so the dependency check
        # must cover both forms or a FK spell like ``asset_type_id`` could
        # bypass the dynamic validation (a fail-open gap in a fail-closed
        # guard).
        applicable = set(dependencies)
        meta = getattr(sender, "_meta", None)
        if meta is not None:
            for name in dependencies:
                try:
                    applicable.add(meta.get_field(name).attname)
                except FieldDoesNotExist:
                    continue
        if (
            update_fields is not None
            and "custom_field_data" not in update_fields
            and applicable.isdisjoint(update_fields)
        ):
            _clean_without_custom_field_validation(instance)
        else:
            instance.clean()
    except DatabaseError:
        logger.debug("Custom validator skipped (table may not exist yet): %s", sender.__name__)
