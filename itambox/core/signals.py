import logging

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
        dependencies = getattr(sender, "custom_field_data_validation_dependencies", frozenset())
        if (
            update_fields is not None
            and "custom_field_data" not in update_fields
            and dependencies.isdisjoint(update_fields)
        ):
            _clean_without_custom_field_validation(instance)
        else:
            instance.clean()
    except DatabaseError:
        logger.debug("Custom validator skipped (table may not exist yet): %s", sender.__name__)
