import logging

from django.core.exceptions import (
    FieldDoesNotExist,
    MultipleObjectsReturned,
    ObjectDoesNotExist,
    ValidationError,
)
from django.db.models.fields.related import ManyToOneRel, RelatedField
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from rest_framework.serializers import ListSerializer
from rest_framework.views import get_view_name as drf_get_view_name

from core.api.exceptions import SerializerNotFound
from itambox.api.fields import RelatedObjectCountField
from core.querysets import count_related

logger = logging.getLogger('itambox.utilities.api')


def get_serializer_for_model(model, prefix=''):
    app_label, model_name = model._meta.label.split('.')
    serializer_name = f'{app_label}.api.serializers.{prefix}{model_name}Serializer'
    try:
        return import_string(serializer_name)
    except ImportError:
        raise SerializerNotFound(
            f"Could not determine serializer for {app_label}.{model_name} with prefix '{prefix}'"
        )


def get_view_name(view):
    if hasattr(view, 'queryset') and view.queryset is not None:
        name = title(view.queryset.model._meta.verbose_name)
        if suffix := getattr(view, 'suffix', None):
            name = f'{name} {suffix}'
        return name
    if hasattr(view, 'api_root_dict'):
        if hasattr(view, 'get_view_name') and not getattr(view, '_ignore_viewset_name', False):
            return view.get_view_name()
        return 'API Root'
    return drf_get_view_name(view)


def _get_nested_serializer(serializer_field):
    from core.api.base import BaseModelSerializer
    if isinstance(serializer_field, ListSerializer):
        serializer_field = serializer_field.child
    if isinstance(serializer_field, BaseModelSerializer):
        return serializer_field
    return None


def _get_serializer_fields(serializer_field):
    if isinstance(serializer_field, ListSerializer):
        serializer_field = serializer_field.child
    fields = getattr(serializer_field, '_include_fields', None) or serializer_field.Meta.fields
    omit = getattr(serializer_field, '_omit_fields', []) or []
    return [field_name for field_name in fields if field_name not in omit]


def get_prefetches_for_serializer(serializer_class, fields=None, omit=None):
    if fields is not None and omit is not None:
        raise TypeError("Cannot specify both 'fields' and 'omit' parameters.")

    model = serializer_class.Meta.model
    fields_to_include = fields or serializer_class.Meta.fields
    fields_to_omit = omit or []

    prefetch_fields = []
    for field_name in fields_to_include:
        if field_name in fields_to_omit:
            continue
        serializer_field = serializer_class._declared_fields.get(field_name)

        model_field_name = field_name
        if serializer_field and getattr(serializer_field, 'source', None):
            model_field_name = serializer_field.source

        try:
            field = model._meta.get_field(model_field_name)
            if isinstance(field, (RelatedField, ManyToOneRel, GenericForeignKey)):
                prefetch_fields.append(field.name)
        except FieldDoesNotExist:
            continue

        if nested_serializer := _get_nested_serializer(serializer_field):
            subfields = _get_serializer_fields(nested_serializer)
            for subfield in get_prefetches_for_serializer(type(nested_serializer), fields=subfields):
                prefetch_fields.append(f'{field.name}__{subfield}')

    return prefetch_fields


def get_annotations_for_serializer(serializer_class, fields=None, omit=None):
    if fields is not None and omit is not None:
        raise TypeError("Cannot specify both 'fields' and 'omit' parameters.")

    model = serializer_class.Meta.model
    fields_to_include = fields or serializer_class.Meta.fields
    fields_to_omit = omit or []

    annotations = {}
    for field_name, field in serializer_class._declared_fields.items():
        if field_name in fields_to_omit:
            continue
        if field_name in fields_to_include and type(field) is RelatedObjectCountField:
            related_field = getattr(model, field.relation).field
            annotations[field_name] = count_related(related_field.model, related_field.name)

    return annotations


def get_related_object_by_attrs(queryset, attrs):
    if attrs is None:
        return None

    if isinstance(attrs, dict):
        params = _dict_to_filter_params(attrs)
        try:
            return queryset.get(**params)
        except ObjectDoesNotExist:
            raise ValidationError(
                _("Related object not found using the provided attributes: {params}").format(params=params))
        except MultipleObjectsReturned:
            raise ValidationError(
                _("Multiple objects match the provided attributes: {params}").format(params=params)
            )

    try:
        pk = int(attrs)
    except (TypeError, ValueError):
        raise ValidationError(
            _("Related objects must be referenced by numeric ID or by dictionary of attributes. Received an "
              "unrecognized value: {value}").format(value=attrs)
        )

    try:
        return queryset.get(pk=pk)
    except ObjectDoesNotExist:
        raise ValidationError(_("Related object not found using the provided numeric ID: {id}").format(id=pk))


def _dict_to_filter_params(d):
    return {f"{k}__in" if isinstance(v, list) else k: v for k, v in d.items()}


def title(s):
    return s.replace('_', ' ').title()
