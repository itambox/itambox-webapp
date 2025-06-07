from django.utils.translation import gettext as _
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType

__all__ = (
    'ContentTypeField',
)

class ContentTypeField(serializers.RelatedField):
    """
    Represent a ContentType as '<app_label>.<model>'
    Based on NetBox implementation.
    """
    default_error_messages = {
        "does_not_exist": _("Invalid content type: {content_type}"),
        "invalid": _("Invalid value. Specify a content type as '<app_label>.<model_name>'."),
    }

    def __init__(self, **kwargs):
        # Accept queryset argument for compatibility, but use ContentType manager directly
        kwargs.pop('queryset', None)
        self.queryset = ContentType.objects.all()
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('invalid')
        try:
            app_label, model = data.split('.')
            return self.queryset.get(app_label=app_label, model=model)
        except ObjectDoesNotExist:
            self.fail('does_not_exist', content_type=data)
        except (AttributeError, TypeError, ValueError):
            self.fail('invalid')

    def to_representation(self, obj):
        return f"{obj.app_label}.{obj.model}" 