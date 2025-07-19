from functools import cached_property

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.api.exceptions import SerializerNotFound


class BaseModelSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='')
    display = serializers.SerializerMethodField(read_only=True)

    def __init__(self, *args, nested=False, fields=None, omit=None, **kwargs):
        self.nested = nested
        self._include_fields = fields or []
        self._omit_fields = omit or []

        if self.nested:
            self.validators = []

        if self.nested and not fields and not omit:
            self._include_fields = getattr(self.Meta, 'brief_fields', ())

        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        if self.nested:
            queryset = self.Meta.model.objects.all()
            from core.api.utils import get_related_object_by_attrs
            return get_related_object_by_attrs(queryset, data)
        return super().to_internal_value(data)

    @cached_property
    def fields(self):
        fields = super().fields

        if self._include_fields:
            for field_name in set(fields) - set(self._include_fields):
                fields.pop(field_name, None)

        for field_name in set(self._omit_fields):
            fields.pop(field_name, None)

        return fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_display(self, obj):
        return str(obj)


class ValidatedModelSerializer(BaseModelSerializer):
    def get_unique_together_constraints(self, model):
        return []

    def validate(self, data):
        if self.nested:
            return data

        attrs = data.copy()
        opts = self.Meta.model._meta
        m2m_values = {}
        for field in [*opts.local_many_to_many, *opts.related_objects]:
            if field.name in attrs:
                m2m_values[field.name] = attrs.pop(field.name)

        if self.instance is None:
            instance = self.Meta.model(**attrs)
        else:
            instance = self.instance
            for k, v in attrs.items():
                setattr(instance, k, v)
        instance._m2m_values = m2m_values
        instance.full_clean(validate_unique=False)

        if 'custom_field_data' in attrs:
            data['custom_field_data'] = instance.custom_field_data

        return data
