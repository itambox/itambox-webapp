"""Explicit, read-time tenant scoping for model-choice form fields (#584, WP2).

This module is the explicit replacement for the global
``ModelChoiceField.queryset`` patch installed by ``CoreConfig.ready()``. It is
not used by any domain form yet (WP3 migrates them); the global patch stays
installed until WP5, so nothing here changes runtime behaviour.

The mechanism
=============

Django's ``ModelChoiceField.queryset`` is a property. Choice rendering,
``__deepcopy__`` and bound validation (``to_python`` /
``ModelMultipleChoiceField._check_values``) all *read* ``self.queryset``, so
scoping has to happen on every read, not once at assignment time. The field
classes below re-declare the property so that each read applies
``filter_by_tenant()`` against the ambient scope (read-then-validate), through
ordinary public subclassing and without monkey-patching.

Consequences worth knowing:

* Form construction deep-copies each field through the scoped getter, so the
  stored queryset of a form instance is already narrowed to the scope that was
  active at construction. Later reads intersect that snapshot with the then
  ambient scope. This is the same behaviour the global patch has today.
* A queryset whose class has no ``filter_by_tenant`` (for example the user
  model) is returned untouched.

Three ways to opt a field in
============================

1. Declare ``TenantScopedModelChoiceField`` / ``TenantScopedModelMultipleChoiceField``
   directly on a form (or via ``Meta.field_classes``).
2. Call ``apply_tenant_scoped_choices(form, exclude=())`` after the form's fields
   exist. This covers auto-generated ``ModelForm`` fields without per-field
   boilerplate. Opt-outs are explicit field names in ``exclude``; no class-name
   matching is involved.
3. Use ``TenantScopedFormMixin`` (``core.forms.base``), which calls (2).

Fields added to ``form.fields`` after the helper ran (for example in a subclass
``__init__`` after ``super().__init__()``) are not rewritten automatically;
re-run the helper to cover them, or declare a scoped field class for them.

For filter sets, ``TenantScopedFilterSetMixin`` applies the same field classes to
the model-choice filters; for the Django admin, ``TenantScopedAdminFormMixin``
wraps the generated admin form.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import django_filters
from django import forms

_SCOPED_MARKER = "_tenant_scoped_queryset"


def _read_queryset(field: forms.ModelChoiceField) -> Any:
    """Return the field's stored queryset through Django's own getter."""
    return forms.ModelChoiceField.queryset.fget(field)  # type: ignore[attr-defined]


def _supports_scoping(queryset: Any) -> bool:
    """Whether ``queryset`` can be narrowed to the ambient scope."""
    return queryset is not None and hasattr(queryset, "filter_by_tenant")


class TenantScopedQuerysetMixin:
    """Re-apply ``filter_by_tenant()`` every time ``queryset`` is read.

    Mix into a ``ModelChoiceField`` subclass *before* the Django base. The
    setter is Django's own (it also refreshes ``widget.choices``).
    """

    _tenant_scoped_queryset = True

    def _get_scoped_queryset(self) -> Any:
        queryset = _read_queryset(self)
        if _supports_scoping(queryset):
            return queryset.filter_by_tenant()
        return queryset

    def _set_scoped_queryset(self, queryset: Any) -> None:
        forms.ModelChoiceField.queryset.fset(self, queryset)  # type: ignore[attr-defined]

    queryset = property(_get_scoped_queryset, _set_scoped_queryset)


class TenantScopedModelChoiceField(TenantScopedQuerysetMixin, forms.ModelChoiceField):
    """``ModelChoiceField`` whose choices and validation follow the ambient scope."""


class TenantScopedModelMultipleChoiceField(TenantScopedQuerysetMixin, forms.ModelMultipleChoiceField):
    """``ModelMultipleChoiceField`` whose choices and validation follow the ambient scope."""


_SCOPED_CLASS_CACHE: dict[type, type] = {}


def is_tenant_scoped_field(field: Any) -> bool:
    """Whether ``field`` already re-scopes its queryset on every read."""
    return bool(getattr(type(field), _SCOPED_MARKER, False))


def scoped_field_class(field_class: type) -> type:
    """Return a read-time scoped subclass of a model-choice field class.

    The result keeps the original class in its MRO, so project or third-party
    field subclasses keep their behaviour. Results are cached per class.
    """
    if getattr(field_class, _SCOPED_MARKER, False):
        return field_class
    cached = _SCOPED_CLASS_CACHE.get(field_class)
    if cached is None:
        cached = type(
            f"TenantScoped{field_class.__name__}",
            (TenantScopedQuerysetMixin, field_class),
            {"__module__": __name__},
        )
        _SCOPED_CLASS_CACHE[field_class] = cached
    return cached


def scope_field(field: forms.ModelChoiceField) -> bool:
    """Switch one existing field to read-time scoping, keeping all its state.

    Returns ``True`` when the field was converted. Fields that are already
    scoped, or whose queryset has no ``filter_by_tenant()``, are left alone.
    """
    if is_tenant_scoped_field(field) or not _supports_scoping(_read_queryset(field)):
        return False
    field.__class__ = scoped_field_class(type(field))
    return True


def apply_tenant_scoped_choices(form: forms.BaseForm, exclude: Iterable[str] = ()) -> list[str]:
    """Rewrite every tenant-capable model-choice field of ``form`` to read-time scoping.

    Covers auto-generated ``ModelForm`` fields as well as declared ones. ``exclude``
    is an explicit collection of field names that must stay non-scoped (for
    deliberately unscoped ``_base_manager`` pickers, source pools, ``.none()``
    placeholders). Returns the names that were converted.
    """
    excluded = frozenset(exclude)
    unknown = excluded - set(form.fields)
    if unknown:
        # An exclusion that names no field is a typo that would silently scope
        # a field the author meant to leave alone.
        raise ValueError(f"{type(form).__name__}: tenant scoping exclusions name unknown fields: {sorted(unknown)}")
    converted = []
    for name, field in form.fields.items():
        if name in excluded or not isinstance(field, forms.ModelChoiceField):
            continue
        if scope_field(field):
            converted.append(name)
    return converted


class TenantScopedFilterSetMixin:
    """Read-time scoping for the model-choice filters of a ``django_filters.FilterSet``.

    Place before ``FilterSet`` in the bases. After the filter set is built, every
    ``ModelChoiceFilter`` / ``ModelMultipleChoiceFilter`` with a static,
    tenant-capable queryset gets a scoped field class, so both the rendered
    choices and bound filter validation follow the ambient scope (tenant, tenant
    group, all-accessible).

    Callable querysets are resolved per request by django-filter and are left
    alone; list a filter name in ``tenant_scoped_filter_exclusions`` to keep any
    other filter non-scoped.
    """

    tenant_scoped_filter_exclusions: tuple[str, ...] = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[misc]
        self._apply_tenant_scoped_filters()

    def _apply_tenant_scoped_filters(self) -> None:
        filters = self.filters  # type: ignore[attr-defined]
        unknown = set(self.tenant_scoped_filter_exclusions) - set(filters)
        if unknown:
            raise ValueError(
                f"{type(self).__name__}: tenant scoping exclusions name unknown filters: {sorted(unknown)}"
            )
        for name, filter_obj in filters.items():
            if name not in self.tenant_scoped_filter_exclusions:
                _scope_filter(filter_obj)


def _scope_filter(filter_obj: Any) -> None:
    """Give one model-choice filter a scoped field class (static querysets only)."""
    if not isinstance(filter_obj, (django_filters.ModelChoiceFilter, django_filters.ModelMultipleChoiceFilter)):
        return
    queryset = filter_obj.extra.get("queryset", getattr(filter_obj, "queryset", None))
    if callable(queryset) or not _supports_scoping(queryset):
        return
    cached_field = getattr(filter_obj, "_field", None)
    if cached_field is not None:
        scope_field(cached_field)
    filter_obj.field_class = scoped_field_class(filter_obj.field_class)


class TenantScopedAdminFormMixin:
    """``ModelAdmin`` mixin that scopes the generated admin form's model choices.

    Opt-in per admin (the hook for WP3). Fields named in
    ``tenant_scoped_choice_exclusions`` stay non-scoped.
    """

    tenant_scoped_choice_exclusions: tuple[str, ...] = ()

    def get_form(self, request: Any, obj: Any = None, **kwargs: Any) -> type:
        base = super().get_form(request, obj, **kwargs)  # type: ignore[misc]
        # Imported here only to avoid a module-level dependency on form bases for
        # callers that never use the admin hook.
        return _admin_scoped_form_class(base, tuple(self.tenant_scoped_choice_exclusions))


_ADMIN_FORM_CACHE: dict[tuple[type, tuple[str, ...]], type] = {}


def _admin_scoped_form_class(base: type, exclusions: tuple[str, ...]) -> type:
    key = (base, exclusions)
    cached = _ADMIN_FORM_CACHE.get(key)
    if cached is None:
        cached = type(base.__name__, (_ScopedChoicesInit, base), {"_scoped_exclusions": exclusions})
        _ADMIN_FORM_CACHE[key] = cached
    return cached


class _ScopedChoicesInit:
    """Cooperative ``__init__`` that applies the scoping helper (admin forms)."""

    _scoped_exclusions: tuple[str, ...] = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        apply_tenant_scoped_choices(self, exclude=self._scoped_exclusions)  # type: ignore[arg-type]
