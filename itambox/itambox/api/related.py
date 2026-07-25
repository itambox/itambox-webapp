"""Resolution of related objects referenced by ID or by attribute dictionary.

A leaf module by design: it depends on Django and DRF exception types only.
``itambox.api.base`` needs this lookup while ``itambox.api.utils`` needs the
serializer base class, and keeping the helper here is what stops those two from
importing each other.
"""

from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist, ValidationError
from django.utils.translation import gettext_lazy as _

__all__ = ("get_related_object_by_attrs",)


def get_related_object_by_attrs(queryset, attrs):
    if attrs is None:
        return None

    if isinstance(attrs, dict):
        params = _dict_to_filter_params(attrs)
        try:
            return queryset.get(**params)
        except ObjectDoesNotExist as exc:
            raise ValidationError(
                _("Related object not found using the provided attributes: {params}").format(params=params)
            ) from exc
        except MultipleObjectsReturned as exc:
            raise ValidationError(
                _("Multiple objects match the provided attributes: {params}").format(params=params)
            ) from exc

    try:
        pk = int(attrs)
    except (TypeError, ValueError):
        raise ValidationError(
            _(
                "Related objects must be referenced by numeric ID or by dictionary of attributes. Received an "
                "unrecognized value: {value}"
            ).format(value=attrs)
        ) from None

    try:
        return queryset.get(pk=pk)
    except ObjectDoesNotExist as exc:
        raise ValidationError(_("Related object not found using the provided numeric ID: {id}").format(id=pk)) from exc


def _dict_to_filter_params(d):
    return {f"{k}__in" if isinstance(v, list) else k: v for k, v in d.items()}
