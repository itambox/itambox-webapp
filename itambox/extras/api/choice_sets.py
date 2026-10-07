"""REST transport for choice-set and choice definition commands.

Every write goes through ``extras.services.definition_commands``; this module only
parses input, projects results and maps rejections onto the shared error envelope.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from assets.api.specification_api import (
    StrictInputSerializer,
    error_response,
    etag_for_revision,
    if_match_revision,
    missing_precondition_response,
)
from extras.models import CustomFieldChoice, CustomFieldChoiceSet
from extras.services._definition_command_support import issue, resource_revision_for_definition
from extras.services.choice_reorder import reorder_custom_field_choices
from extras.services.definition_command_contracts import (
    CustomFieldChoiceCreateInputDTO,
    CustomFieldChoiceSetCreateInputDTO,
    CustomFieldChoiceSetUpdateInputDTO,
    CustomFieldChoiceUpdateInputDTO,
    DefinitionRejectedDTO,
)
from extras.services.definition_commands import (
    create_custom_field_choice,
    create_custom_field_choice_set,
    deprecate_custom_field_choice,
    deprecate_custom_field_choice_set,
    update_custom_field_choice,
    update_custom_field_choice_set,
)
from extras.services.specifications.contracts import ResourceRevision
from itambox.api.permissions import TokenPermissions
from itambox.api.viewsets import ITAMBoxReadOnlyModelViewSet
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor
from organization.services.catalogue_authorization import has_provider_catalogue_permission

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class DefinitionActionPermissions(TokenPermissions):
    """Global-catalogue rule shared with the UI.

    Reads use the ordinary token permission. Writes require the model permission in
    the provider scope (``has_provider_catalogue_permission``), so a tenant-scoped
    grant never authorizes a write to a global definition. Create actions need
    ``add_*``; adding a choice to a set needs ``add_customfieldchoice``; every other
    write needs ``change_*``.
    """

    def _write_permissions(self, view, model):
        label = model._meta.app_label
        action_name = getattr(view, "action", None)
        if action_name == "create":
            return [f"{label}.add_{model._meta.model_name}"]
        if action_name == "destroy":
            return [f"{label}.delete_{model._meta.model_name}"]
        if action_name == "choices":
            return [f"{label}.add_customfieldchoice"]
        return [f"{label}.change_{model._meta.model_name}"]

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in _SAFE_METHODS:
            return super().has_permission(request, view)
        model = self._queryset(view).model
        return all(
            has_provider_catalogue_permission(request.user, perm) for perm in self._write_permissions(view, model)
        )

    def has_object_permission(self, request, view, obj):
        if request.method in _SAFE_METHODS:
            return super().has_object_permission(request, view, obj)
        return self.has_permission(request, view)


def actor_for(user) -> ActorContextDTO:
    return ActorContextDTO(actor_id=int(user.pk), authentication_revision=authentication_revision_for_actor(user))


class ChoiceReadSerializer(serializers.ModelSerializer):
    resource_revision = serializers.SerializerMethodField()

    class Meta:
        model = CustomFieldChoice
        fields = ["id", "choice_set", "key", "label", "position", "lifecycle", "replaced_by", "resource_revision"]
        read_only_fields = fields

    def get_resource_revision(self, obj) -> str:
        return str(resource_revision_for_definition(obj))


class ChoiceSetReadSerializer(serializers.ModelSerializer):
    choices = ChoiceReadSerializer(many=True, read_only=True)
    resource_revision = serializers.SerializerMethodField()

    class Meta:
        model = CustomFieldChoiceSet
        fields = ["id", "namespace", "slug", "label", "lifecycle", "replaced_by", "choices", "resource_revision"]
        read_only_fields = fields

    def get_resource_revision(self, obj) -> str:
        return str(resource_revision_for_definition(obj))


class ChoiceEntryInputSerializer(StrictInputSerializer):
    key = serializers.CharField()
    label = serializers.CharField(allow_blank=True)


class ChoiceSetCreateInputSerializer(StrictInputSerializer):
    namespace = serializers.CharField()
    slug = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    choices = ChoiceEntryInputSerializer(many=True, required=False)


class LabelInputSerializer(StrictInputSerializer):
    label = serializers.CharField(allow_blank=True)


class EmptyInputSerializer(StrictInputSerializer):
    pass


class ReorderInputSerializer(StrictInputSerializer):
    keys = serializers.ListField(child=serializers.CharField(), allow_empty=True)


class _Rollback(Exception):
    def __init__(self, result):
        super().__init__("definition command rejected")
        self.result = result


def _command_error(result) -> Response | None:
    if isinstance(result, DefinitionRejectedDTO):
        return error_response(result.issues)
    return None


def _revision_or_response(request):
    revision = if_match_revision(request)
    if revision is None:
        return None, missing_precondition_response(("If-Match",))
    return revision, None


class _RevisionETagMixin:
    @staticmethod
    def _get_etag(obj):
        if isinstance(obj, (CustomFieldChoice, CustomFieldChoiceSet)):
            return etag_for_revision(resource_revision_for_definition(obj))
        return None


class CustomFieldChoiceSetViewSet(_RevisionETagMixin, ITAMBoxReadOnlyModelViewSet):
    queryset = CustomFieldChoiceSet.objects.prefetch_related("choices").all()
    serializer_class = ChoiceSetReadSerializer
    permission_classes = [DefinitionActionPermissions]

    def _present(self, choice_set_id, *, success_status=status.HTTP_200_OK) -> Response:
        fresh = CustomFieldChoiceSet.objects.prefetch_related("choices").get(pk=choice_set_id)
        response = Response(ChoiceSetReadSerializer(fresh).data, status=success_status)
        response["ETag"] = etag_for_revision(resource_revision_for_definition(fresh))
        return response

    @extend_schema(request=ChoiceSetCreateInputSerializer, responses={201: ChoiceSetReadSerializer})
    def create(self, request, *args, **kwargs):
        serializer = ChoiceSetCreateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        entries = data.get("choices", [])
        keys = [entry["key"] for entry in entries]
        if len(set(keys)) != len(keys):
            return error_response((issue("DUPLICATE_FIELD", path=("choices",)),))
        actor = actor_for(request.user)
        try:
            with transaction.atomic():
                result = create_custom_field_choice_set(
                    actor=actor,
                    definition=CustomFieldChoiceSetCreateInputDTO(
                        namespace=data["namespace"], slug=data["slug"], label=data["label"]
                    ),
                )
                if isinstance(result, DefinitionRejectedDTO):
                    raise _Rollback(result)
                for position, entry in enumerate(entries, start=1):
                    child = create_custom_field_choice(
                        actor=actor,
                        definition=CustomFieldChoiceCreateInputDTO(
                            choice_set_id=result.definition_id,
                            key=entry["key"],
                            label=entry["label"],
                            position=position,
                        ),
                    )
                    if isinstance(child, DefinitionRejectedDTO):
                        raise _Rollback(child)
        except _Rollback as rollback:
            return error_response(rollback.result.issues)
        except (TypeError, ValueError):
            return error_response((issue("INVALID_TYPE"),))
        return self._present(result.definition_id, success_status=status.HTTP_201_CREATED)

    @extend_schema(request=LabelInputSerializer, responses={200: ChoiceSetReadSerializer})
    def partial_update(self, request, *args, **kwargs):
        serializer = LabelInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revision, missing = _revision_or_response(request)
        if missing is not None:
            return missing
        choice_set = self.get_object()
        try:
            result = update_custom_field_choice_set(
                actor=actor_for(request.user),
                choice_set_id=choice_set.pk,
                expected_resource_revision=ResourceRevision(revision),
                changes=CustomFieldChoiceSetUpdateInputDTO(label=serializer.validated_data["label"]),
            )
        except (TypeError, ValueError):
            return error_response((issue("INVALID_TYPE"),))
        return _command_error(result) or self._present(choice_set.pk)

    @extend_schema(request=EmptyInputSerializer, responses={200: ChoiceSetReadSerializer})
    @action(detail=True, methods=["post"], url_path="deprecate", serializer_class=EmptyInputSerializer)
    def deprecate(self, request, pk=None):
        EmptyInputSerializer(data=request.data).is_valid(raise_exception=True)
        revision, missing = _revision_or_response(request)
        if missing is not None:
            return missing
        choice_set = self.get_object()
        result = deprecate_custom_field_choice_set(
            actor=actor_for(request.user),
            choice_set_id=choice_set.pk,
            expected_resource_revision=ResourceRevision(revision),
        )
        return _command_error(result) or self._present(choice_set.pk)

    @extend_schema(request=ChoiceEntryInputSerializer, responses={201: ChoiceReadSerializer})
    @action(detail=True, methods=["post"], url_path="choices", serializer_class=ChoiceEntryInputSerializer)
    def choices(self, request, pk=None):
        serializer = ChoiceEntryInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revision, missing = _revision_or_response(request)
        if missing is not None:
            return missing
        choice_set = self.get_object()
        if str(resource_revision_for_definition(choice_set)) != revision:
            return error_response((issue("STALE_RESOURCE", message_key="specifications.stale_resource"),))
        last = choice_set.choices.aggregate(last=Max("position"))["last"] or 0
        try:
            result = create_custom_field_choice(
                actor=actor_for(request.user),
                definition=CustomFieldChoiceCreateInputDTO(
                    choice_set_id=choice_set.pk,
                    key=serializer.validated_data["key"],
                    label=serializer.validated_data["label"],
                    position=last + 1,
                ),
            )
        except (TypeError, ValueError):
            return error_response((issue("INVALID_TYPE"),))
        if isinstance(result, DefinitionRejectedDTO):
            return error_response(result.issues)
        choice = CustomFieldChoice.objects.get(pk=result.definition_id)
        response = Response(ChoiceReadSerializer(choice).data, status=status.HTTP_201_CREATED)
        fresh_set = CustomFieldChoiceSet.objects.get(pk=choice_set.pk)
        response["ETag"] = etag_for_revision(resource_revision_for_definition(fresh_set))
        return response

    @extend_schema(request=ReorderInputSerializer, responses={200: ChoiceSetReadSerializer})
    @action(detail=True, methods=["post"], url_path="reorder", serializer_class=ReorderInputSerializer)
    def reorder(self, request, pk=None):
        serializer = ReorderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revision, missing = _revision_or_response(request)
        if missing is not None:
            return missing
        choice_set = self.get_object()
        result = reorder_custom_field_choices(
            actor=actor_for(request.user),
            choice_set_id=choice_set.pk,
            expected_resource_revision=revision,
            keys=serializer.validated_data["keys"],
        )
        return _command_error(result) or self._present(choice_set.pk)


class CustomFieldChoiceViewSet(_RevisionETagMixin, ITAMBoxReadOnlyModelViewSet):
    queryset = CustomFieldChoice.objects.select_related("choice_set").all()
    serializer_class = ChoiceReadSerializer
    permission_classes = [DefinitionActionPermissions]

    def _present(self, choice_id) -> Response:
        fresh = CustomFieldChoice.objects.get(pk=choice_id)
        response = Response(ChoiceReadSerializer(fresh).data)
        response["ETag"] = etag_for_revision(resource_revision_for_definition(fresh))
        return response

    @extend_schema(request=LabelInputSerializer, responses={200: ChoiceReadSerializer})
    def partial_update(self, request, *args, **kwargs):
        serializer = LabelInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revision, missing = _revision_or_response(request)
        if missing is not None:
            return missing
        choice = self.get_object()
        try:
            result = update_custom_field_choice(
                actor=actor_for(request.user),
                choice_id=choice.pk,
                expected_resource_revision=ResourceRevision(revision),
                changes=CustomFieldChoiceUpdateInputDTO(label=serializer.validated_data["label"]),
            )
        except (TypeError, ValueError):
            return error_response((issue("INVALID_TYPE"),))
        return _command_error(result) or self._present(choice.pk)

    @extend_schema(request=EmptyInputSerializer, responses={200: ChoiceReadSerializer})
    @action(detail=True, methods=["post"], url_path="deprecate", serializer_class=EmptyInputSerializer)
    def deprecate(self, request, pk=None):
        EmptyInputSerializer(data=request.data).is_valid(raise_exception=True)
        revision, missing = _revision_or_response(request)
        if missing is not None:
            return missing
        choice = self.get_object()
        result = deprecate_custom_field_choice(
            actor=actor_for(request.user),
            choice_id=choice.pk,
            expected_resource_revision=ResourceRevision(revision),
        )
        return _command_error(result) or self._present(choice.pk)
