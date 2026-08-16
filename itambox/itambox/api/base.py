from collections.abc import Iterator, Mapping, Sequence
from functools import cached_property
from typing import Any

from django.db.models import Manager
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.utils.serializer_helpers import BindingDict

from itambox.api.exceptions import SerializerNotFound
from itambox.api.related import get_related_object_by_attrs


def reject_unknown_or_writableless(
    submitted: Mapping[str, object], fields: Mapping[str, serializers.Field[Any]]
) -> None:
    """Reject payloads with unknown fields or without any writable field.

    Shared by the single-serializer and bulk (ListSerializer) validation paths so
    both reject malformed input before any database row can be created.
    """
    submitted_keys = set(submitted.keys())
    unknown_fields = submitted_keys - set(fields)
    if unknown_fields:
        raise serializers.ValidationError({field: _("Unknown field.") for field in sorted(unknown_fields)})

    writable_fields = {name for name, field in fields.items() if not field.read_only}
    if submitted_keys.isdisjoint(writable_fields):
        raise serializers.ValidationError(_("At least one writable field is required."))


# typing: third-party-untyped: DRF base serializer intentionally remains open over concrete child models
class BaseModelSerializer(serializers.ModelSerializer[Any]):
    url = serializers.HyperlinkedIdentityField(view_name="")
    display = serializers.SerializerMethodField(read_only=True)

    # typing: third-party-untyped: DRF constructor accepts parser-native and many-mode kwargs
    def __init__(
        self,
        *args: Any,
        nested: bool = False,
        fields: Sequence[str] | None = None,
        omit: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.nested = nested
        self._include_fields = fields or []
        self._omit_fields = omit or []

        if self.nested:
            self.validators = []

        if self.nested and not fields and not omit:
            self._include_fields = getattr(self.Meta, "brief_fields", ())

        super().__init__(*args, **kwargs)

    # typing: third-party-untyped: DRF passes parser-native input before serializer validation
    def to_internal_value(self, data: Any) -> object:
        if self.nested:
            queryset = self.Meta.model.objects.all()
            return get_related_object_by_attrs(queryset, data)
        return super().to_internal_value(data)

    @cached_property
    def fields(self) -> BindingDict:
        fields = super().fields

        if self._include_fields:
            for field_name in set(fields) - set(self._include_fields):
                fields.pop(field_name, None)

        for field_name in set(self._omit_fields):
            fields.pop(field_name, None)

        return fields

    @extend_schema_field(OpenApiTypes.STR)
    # typing: third-party-untyped: DRF model serializer hooks accept each concrete child model
    def get_display(self, obj: Any) -> str:
        return str(obj)


class ValidatedModelSerializer(BaseModelSerializer):
    # typing: third-party-untyped: DRF supplies model metadata through an unparameterized hook
    def get_unique_together_constraints(self, model: Any) -> Iterator[tuple[set[tuple[str, ...]], Manager[Any]]]:
        return iter(())

    def validate(self, data: dict[str, object]) -> dict[str, object]:
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

        if "custom_field_data" in attrs:
            data["custom_field_data"] = instance.custom_field_data

        return data
