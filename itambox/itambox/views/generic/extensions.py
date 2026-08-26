"""Request-time orchestration for generic-presentation providers."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import NON_FIELD_ERRORS, ImproperlyConfigured
from django.db.models import Model, QuerySet
from django.http import HttpRequest, QueryDict

from itambox.registry import (
    GENERIC_PRESENTATION_DETAIL_FEATURES,
    DetailContextInput,
    GenericPresentationRegistration,
    ListContextInput,
    ListFilterInput,
    ListParamsInput,
    ListParamsResult,
    registry,
    validate_list_filter_result,
)


@dataclass(frozen=True, slots=True)
class ResolvedListPresentation:
    request: HttpRequest
    model: type[Model]
    content_type: ContentType
    params: QueryDict
    partial: bool
    provider_state: Mapping[str, Mapping[str, object]]


def _freeze_params(params: QueryDict) -> QueryDict:
    frozen = params.copy()
    frozen._mutable = False
    return frozen


def _changed_param_keys(before: QueryDict, after: QueryDict) -> set[str]:
    return {key for key in set(before.keys()) | set(after.keys()) if before.getlist(key) != after.getlist(key)}


def _validated_params_result(
    registration: GenericPresentationRegistration,
    current: QueryDict,
    result: object,
) -> tuple[QueryDict, Mapping[str, object]]:
    if not isinstance(result, ListParamsResult):
        raise ImproperlyConfigured(f"Generic presentation provider {registration.name!r} must return ListParamsResult")
    if result.params is not current and not isinstance(result.params, QueryDict):
        raise ImproperlyConfigured(
            f"Generic presentation provider {registration.name!r} must return QueryDict parameters"
        )
    if not isinstance(result.state, Mapping):
        raise ImproperlyConfigured(f"Generic presentation provider {registration.name!r} must return mapping state")
    state = dict(result.state)
    if any(not isinstance(key, str) for key in state):
        raise ImproperlyConfigured(
            f"Generic presentation provider {registration.name!r} returned a non-string state key"
        )
    return _freeze_params(result.params), MappingProxyType(state)


def resolve_list_provider_params(
    request: HttpRequest,
    model: type[Model],
    *,
    partial: bool,
) -> ResolvedListPresentation:
    """Resolve one ContentType and run the plural parameter pipeline."""
    content_type = ContentType.objects.get_for_model(model)
    current = _freeze_params(request.GET)
    changed_by = {}
    provider_state = {}

    for registration in registry.generic_presentation_registrations():
        if not registration.list_params:
            continue
        result = registration.provider.resolve_list_params(
            ListParamsInput(
                request=request,
                model=model,
                params=current,
                content_type=content_type,
                partial=partial,
            )
        )
        next_params, state = _validated_params_result(registration, current, result)
        for key in _changed_param_keys(current, next_params):
            previous_owner = changed_by.get(key)
            if previous_owner is not None:
                raise ImproperlyConfigured(
                    f"Generic presentation providers {previous_owner!r} and {registration.name!r} "
                    f"both changed parameter {key!r}"
                )
            changed_by[key] = registration.name
        provider_state[registration.name] = state
        current = next_params

    return ResolvedListPresentation(
        request=request,
        model=model,
        content_type=content_type,
        params=current,
        partial=partial,
        provider_state=MappingProxyType(provider_state),
    )


def _provider_state_for(
    resolution: ResolvedListPresentation,
    registration: GenericPresentationRegistration,
) -> Mapping[str, object]:
    return resolution.provider_state.get(registration.name, MappingProxyType({}))


def filter_list_provider_queryset(
    resolution: ResolvedListPresentation,
    queryset: QuerySet[Model],
) -> QuerySet[Model]:
    """Apply every opted-in provider to the supplied validated queryset."""
    current = queryset
    for registration in registry.generic_presentation_registrations():
        if not registration.list_filter:
            continue
        result = registration.provider.filter_list_queryset(
            ListFilterInput(
                request=resolution.request,
                model=resolution.model,
                params=resolution.params,
                queryset=current,
                content_type=resolution.content_type,
                partial=resolution.partial,
                state=_provider_state_for(resolution, registration),
            )
        )
        current = validate_list_filter_result(registration.name, current, result)
    return current


def _merge_filterset_errors_into_form(filterset_form, filterset) -> None:
    form_errors = filterset_form.errors.as_data()
    for field_name, errors in filterset.errors.as_data().items():
        target_field = field_name if field_name in filterset_form.fields else None
        target_key = target_field or NON_FIELD_ERRORS
        existing_messages = {
            str(message) for existing_error in form_errors.get(target_key, ()) for message in existing_error.messages
        }
        for error in errors:
            error_messages = {str(message) for message in error.messages}
            if not error_messages.issubset(existing_messages):
                filterset_form.add_error(target_field, error)
                existing_messages.update(error_messages)


def validate_generic_display_form(filterset_form, filterset, params: QueryDict) -> bool:
    """Validate the bound generic filters and preserve divergent form errors."""
    filter_is_valid = filterset.is_valid() if filterset is not None else True
    form_is_valid = filterset_form.is_valid() if filterset_form is not None else True
    if filterset_form is not None and filterset is not None and not filter_is_valid:
        _merge_filterset_errors_into_form(filterset_form, filterset)
    return filter_is_valid and form_is_valid


def _merge_provider_context(
    merged: dict[str, object],
    owners: dict[str, str],
    registration: GenericPresentationRegistration,
    result: object,
) -> None:
    if not isinstance(result, Mapping):
        raise ImproperlyConfigured(f"Generic presentation provider {registration.name!r} must return a context mapping")
    contribution = dict(result)
    for key in contribution:
        if not isinstance(key, str) or not key:
            raise ImproperlyConfigured(
                f"Generic presentation provider {registration.name!r} returned a non-empty string key violation"
            )
        previous_owner = owners.get(key)
        if previous_owner is not None:
            raise ImproperlyConfigured(
                f"Generic presentation context key {key!r} conflicts between {previous_owner!r} "
                f"and {registration.name!r}"
            )
        owners[key] = registration.name
    merged.update(contribution)


def build_list_provider_context(
    resolution: ResolvedListPresentation,
    core_context: Mapping[str, object],
) -> dict[str, object]:
    """Merge plural provider context after the immutable core context exists."""
    merged = dict(core_context)
    owners = {key: "core" for key in merged}
    for registration in registry.generic_presentation_registrations():
        if not registration.list_context:
            continue
        result = registration.provider.build_list_context(
            ListContextInput(
                request=resolution.request,
                model=resolution.model,
                params=resolution.params,
                content_type=resolution.content_type,
                partial=resolution.partial,
                state=_provider_state_for(resolution, registration),
            )
        )
        _merge_provider_context(merged, owners, registration, result)
    return merged


def build_detail_provider_context(
    request: HttpRequest,
    obj: Model,
    content_type: ContentType,
    *,
    core_context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Invoke each active feature owner once and collision-check its context."""
    active_features = frozenset(
        feature for feature in GENERIC_PRESENTATION_DETAIL_FEATURES if registry.model_has_feature(type(obj), feature)
    )
    features_by_owner = {}
    for feature in sorted(active_features):
        owner = registry.generic_presentation_owner_for(feature)
        features_by_owner.setdefault(owner, set()).add(feature)

    merged = dict(core_context or {})
    owners = {key: "core" for key in merged}
    for registration in registry.generic_presentation_registrations():
        owned_features = features_by_owner.pop(registration.name, None)
        if not owned_features:
            continue
        result = registration.provider.build_detail_context(
            DetailContextInput(
                request=request,
                obj=obj,
                content_type=content_type,
                active_features=frozenset(owned_features),
            )
        )
        _merge_provider_context(merged, owners, registration, result)

    if features_by_owner:
        missing_registration = min(features_by_owner)
        raise ImproperlyConfigured(
            f"Generic presentation owner {missing_registration!r} has no registration snapshot entry"
        )
    return merged
