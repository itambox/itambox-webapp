from collections import OrderedDict

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.relations import PrimaryKeyRelatedField, RelatedField


class ChoiceField(serializers.Field):
    """
    Represent a ChoiceField as {'value': <DB value>, 'label': <string>}. Accepts a single value on write.
    """
    def __init__(self, choices, allow_blank=False, **kwargs):
        self.choiceset = choices
        self.allow_blank = allow_blank
        self._choices = dict(list(self.choiceset))

        super().__init__(**kwargs)

    def validate_empty_values(self, data):
        if data is None:
            if self.allow_null:
                return True, None
            data = ''
        return super().validate_empty_values(data)

    def to_representation(self, obj):
        if obj != '':
            return {
                'value': obj,
                'label': self._choices.get(obj, ''),
            }
        return None

    def to_internal_value(self, data):
        if data == '':
            if self.allow_blank:
                return data
            raise ValidationError(_("This field may not be blank."))

        if isinstance(data, (dict, list)):
            raise ValidationError(
                _('Value must be passed directly (e.g. "foo": 123); do not use a dictionary or list.')
            )

        if hasattr(data, 'lower'):
            if data.lower() == 'true':
                data = True
            elif data.lower() == 'false':
                data = False
            else:
                try:
                    data = int(data)
                except ValueError:
                    pass

        try:
            if data in self._choices:
                return data
        except TypeError:
            pass

        raise ValidationError(_("{value} is not a valid choice.").format(value=data))

    @property
    def choices(self):
        return self._choices


@extend_schema_field(OpenApiTypes.STR)
class ContentTypeField(RelatedField):
    """
    Represent a ContentType as '<app_label>.<model>'
    """
    default_error_messages = {
        "does_not_exist": _("Invalid content type: {content_type}"),
        "invalid": _("Invalid value. Specify a content type as '<app_label>.<model_name>'."),
    }

    def to_internal_value(self, data):
        try:
            app_label, model = data.split('.')
            return ContentType.objects.get_by_natural_key(app_label=app_label, model=model)
        except ObjectDoesNotExist:
            self.fail('does_not_exist', content_type=data)
        except (AttributeError, TypeError, ValueError):
            self.fail('invalid')

    def to_representation(self, obj):
        return f"{obj.app_label}.{obj.model}"


class SerializedPKRelatedField(PrimaryKeyRelatedField):
    """
    Extends PrimaryKeyRelatedField to return a serialized object on read.
    """
    def __init__(self, serializer, nested=False, **kwargs):
        self.serializer = serializer
        self.nested = nested
        self.pk_field = kwargs.pop('pk_field', None)
        super().__init__(**kwargs)

    def to_representation(self, value):
        return self.serializer(value, nested=self.nested, context={'request': self.context['request']}).data


@extend_schema_field(OpenApiTypes.INT64)
class RelatedObjectCountField(serializers.ReadOnlyField):
    """
    Represents a read-only integer count of related objects.
    """
    def __init__(self, relation, **kwargs):
        self.relation = relation
        super().__init__(**kwargs)
