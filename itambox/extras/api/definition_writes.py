"""REST writes for custom fields and custom fieldsets, routed through the definition commands."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response

from assets.api.specification_api import (
    StrictInputSerializer,
    error_response,
    etag_for_revision,
    if_match_revision,
    missing_precondition_response,
)
from extras.api.choice_sets import DefinitionActionPermissions, actor_for
from extras.models import CustomField, CustomFieldset
from extras.services._definition_command_support import issue, resource_revision_for_definition
from extras.services.definition_command_contracts import (
    CustomFieldCreateInputDTO,
    CustomFieldsetCreateInputDTO,
    CustomFieldsetUpdateInputDTO,
    CustomFieldUpdateInputDTO,
    DefinitionRejectedDTO,
)
from extras.services.definition_commands import (
    create_custom_field,
    create_custom_fieldset,
    update_custom_field,
    update_custom_fieldset,
)
from extras.services.specifications.contracts import ResourceRevision

_FIELD_TYPES = [choice[0] for choice in CustomField.FIELD_TYPE_CHOICES]
_ACTIVATIONS = CustomField.ACTIVATION_CHOICES


class CustomFieldCreateInputSerializer(StrictInputSerializer):
    namespace = serializers.CharField()
    local_key = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    object_types = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    field_type = serializers.ChoiceField(choices=_FIELD_TYPES, required=False)
    activation = serializers.ChoiceField(choices=_ACTIVATIONS, required=False)
    help_text = serializers.CharField(allow_blank=True, required=False)
    quantity_kind = serializers.CharField(allow_null=True, required=False)
    canonical_unit = serializers.CharField(allow_null=True, required=False)
    minimum_value = serializers.DecimalField(max_digits=48, decimal_places=12, allow_null=True, required=False)
    maximum_value = serializers.DecimalField(max_digits=48, decimal_places=12, allow_null=True, required=False)
    regex = serializers.CharField(allow_null=True, required=False)
    decimal_scale = serializers.IntegerField(allow_null=True, required=False)
    max_values = serializers.IntegerField(allow_null=True, required=False)
    text_max_length = serializers.IntegerField(allow_null=True, required=False)
    validation_rule = serializers.CharField(allow_null=True, required=False)
    required = serializers.BooleanField(required=False)
    nullable = serializers.BooleanField(required=False)
    mappings = serializers.ListField(child=serializers.JSONField(), required=False)
    choice_set = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    replaced_by = serializers.CharField(allow_null=True, required=False)


class CustomFieldUpdateInputSerializer(StrictInputSerializer):
    label = serializers.CharField(allow_blank=True, required=False)
    help_text = serializers.CharField(allow_blank=True, required=False)
    activation = serializers.ChoiceField(choices=_ACTIVATIONS, required=False)
    required = serializers.BooleanField(required=False)
    mappings = serializers.ListField(child=serializers.JSONField(), required=False)
    object_types = serializers.ListField(child=serializers.CharField(), allow_empty=False, required=False)
    replaced_by = serializers.CharField(required=False)


class CustomFieldsetCreateInputSerializer(StrictInputSerializer):
    namespace = serializers.CharField()
    slug = serializers.CharField()
    label = serializers.CharField(allow_blank=True, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    field_identities = serializers.ListField(child=serializers.CharField(), required=False)
    replaced_by = serializers.CharField(allow_null=True, required=False)


class CustomFieldsetUpdateInputSerializer(StrictInputSerializer):
    label = serializers.CharField(allow_blank=True, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    replaced_by = serializers.CharField(required=False)


def _tupled(data, *names):
    out = dict(data)
    for name in names:
        if out.get(name) is not None:
            out[name] = tuple(out[name])
    return out


class CommandBackedWriteMixin:
    """Replace the generic model write path with definition-command calls."""

    permission_classes = [DefinitionActionPermissions]

    @staticmethod
    def _get_etag(obj):
        if isinstance(obj, (CustomField, CustomFieldset)):
            return etag_for_revision(resource_revision_for_definition(obj))
        return None

    def _present(self, pk, *, success_status=status.HTTP_200_OK):
        fresh = self.get_queryset().model.objects.get(pk=pk)
        response = Response(self.get_serializer(fresh).data, status=success_status)
        response["ETag"] = etag_for_revision(resource_revision_for_definition(fresh))
        return response

    @staticmethod
    def _rejected(result):
        if isinstance(result, DefinitionRejectedDTO):
            return error_response(result.issues)
        return None

    def update(self, request, *args, **kwargs):
        if not kwargs.pop("partial", False):
            return Response({"detail": 'Method "PUT" not allowed.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)
        return self._patch(request)

    def partial_update(self, request, *args, **kwargs):
        return self._patch(request)

    def _patch(self, request):
        serializer = self.update_input_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revision = if_match_revision(request)
        if revision is None:
            return missing_precondition_response(("If-Match",))
        instance = self.get_object()
        try:
            result = self._run_update(request, instance, ResourceRevision(revision), serializer.validated_data)
        except (TypeError, ValueError):
            return error_response((issue("INVALID_TYPE"),))
        return self._rejected(result) or self._present(instance.pk)

    def create(self, request, *args, **kwargs):
        serializer = self.create_input_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = self._run_create(request, serializer.validated_data)
        except (TypeError, ValueError):
            return error_response((issue("INVALID_TYPE"),))
        return self._rejected(result) or self._present(result.definition_id, success_status=status.HTTP_201_CREATED)


class CustomFieldWriteMixin(CommandBackedWriteMixin):
    create_input_serializer = CustomFieldCreateInputSerializer
    update_input_serializer = CustomFieldUpdateInputSerializer

    @extend_schema(request=CustomFieldCreateInputSerializer)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(request=CustomFieldUpdateInputSerializer)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(request=CustomFieldUpdateInputSerializer)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def _run_create(self, request, data):
        values = _tupled(data, "object_types", "mappings")
        values["choice_set_id"] = values.pop("choice_set", None)
        values = {key: value for key, value in values.items() if value is not None or key in {"quantity_kind", "canonical_unit", "regex", "validation_rule", "replaced_by"}}
        return create_custom_field(actor=actor_for(request.user), definition=CustomFieldCreateInputDTO(**values))

    def _run_update(self, request, instance, revision, data):
        return update_custom_field(
            actor=actor_for(request.user),
            field_id=instance.pk,
            expected_resource_revision=revision,
            changes=CustomFieldUpdateInputDTO(**_tupled(data, "object_types", "mappings")),
        )


class CustomFieldsetWriteMixin(CommandBackedWriteMixin):
    create_input_serializer = CustomFieldsetCreateInputSerializer
    update_input_serializer = CustomFieldsetUpdateInputSerializer

    @extend_schema(request=CustomFieldsetCreateInputSerializer)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(request=CustomFieldsetUpdateInputSerializer)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(request=CustomFieldsetUpdateInputSerializer)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def _run_create(self, request, data):
        values = {key: value for key, value in _tupled(data, "field_identities").items() if value is not None}
        return create_custom_fieldset(actor=actor_for(request.user), definition=CustomFieldsetCreateInputDTO(**values))

    def _run_update(self, request, instance, revision, data):
        return update_custom_fieldset(
            actor=actor_for(request.user),
            fieldset_id=instance.pk,
            expected_resource_revision=revision,
            changes=CustomFieldsetUpdateInputDTO(**data),
        )
