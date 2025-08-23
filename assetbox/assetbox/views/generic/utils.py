from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured


def get_prerequisite_model(queryset):
    try:
        ct = ContentType.objects.get_by_natural_key(
            queryset.model._meta.app_label,
            queryset.model._meta.model_name,
        )
    except ContentType.DoesNotExist:
        raise ImproperlyConfigured(
            f"ContentType not found for {queryset.model._meta.label}"
        )
    return ct.model_class()
