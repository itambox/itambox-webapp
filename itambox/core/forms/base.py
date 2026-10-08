"""Explicit form behaviours that replace the global ``BaseForm`` patch (#584, WP2).

``CoreConfig.ready()`` currently patches ``BaseForm.__init__`` to (a) mark a
``tenant`` field required and (b) add ``data-tom-select`` to select widgets,
deciding the exclusions by class-name substring. This module provides the same
behaviour as explicit, per-form declarations. It is unused by domain forms until
WP3 and the global patch stays installed until WP5.

* ``apply_tom_select`` is the single place that knows which widgets get the
  TomSelect attribute.
* ``TenantScopedFormMixin`` combines read-time choice scoping
  (``core.forms.scoping``), the tenant requiredness declaration and the
  single-accessible-tenant behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from django import forms
from django.apps import apps
from django.db import OperationalError, ProgrammingError

from core.forms.scoping import apply_tenant_scoped_choices
from core.forms.tenant import scope_tenant_field

TOM_SELECT_ATTR = "data-tom-select"
_LISTBOX_CLASSES = ("available-columns", "selected-columns")


def wants_tom_select(widget: forms.Widget) -> bool:
    """Whether ``widget`` is a select that should carry the TomSelect attribute.

    Excluded: non-select widgets, radio and checkbox groups, listboxes (a
    ``size`` attribute) and the column pickers (``available-columns`` /
    ``selected-columns`` CSS classes).
    """
    if not isinstance(widget, (forms.Select, forms.SelectMultiple)):
        return False
    if isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
        return False
    if "size" in widget.attrs:
        return False
    css = widget.attrs.get("class", "")
    return not any(marker in css for marker in _LISTBOX_CLASSES)


def apply_tom_select(fields: Mapping[str, forms.Field] | Iterable[forms.Field]) -> list[str]:
    """Add ``data-tom-select`` to every eligible select widget in ``fields``.

    Accepts ``form.fields`` (a mapping) or any iterable of fields. An existing
    attribute value is preserved. Returns the names (or ``""`` positions for
    plain iterables) of fields that were changed.
    """
    items = list(fields.items()) if isinstance(fields, Mapping) else [("", f) for f in fields]
    changed = []
    for name, field in items:
        if wants_tom_select(field.widget) and TOM_SELECT_ATTR not in field.widget.attrs:
            field.widget.attrs[TOM_SELECT_ATTR] = ""
            changed.append(name)
    return changed


def tenant_rows_exist() -> bool:
    """Whether at least one ``Tenant`` row exists.

    Failures that mean "the table is not there yet" (migrations, a fresh
    database) read as ``False``. Any other error propagates.
    """
    Tenant = apps.get_model("organization", "Tenant")
    try:
        return Tenant.objects.exists()
    except (ProgrammingError, OperationalError):
        return False


class TenantScopedFormMixin:
    """Explicit replacement for the three global form patches.

    Place first in the form's bases. Declarations (all class attributes):

    ``tenant_scoped_choice_exclusions``
        Field names kept non-scoped (deliberate ``_base_manager`` pickers,
        source pools, ``.none()`` placeholders). Unknown names raise.
    ``tenant_required``
        When true and the form has a ``tenant`` field, the field is required as
        soon as one ``Tenant`` row exists. Forms that also carry a
        ``tenant_group`` field declare the tenant-XOR-group rule themselves, so
        the requirement is never applied to them.
    ``tenant_autoset_when_single``
        When true, a non-superuser with exactly one accessible tenant gets the
        field preset, disabled and hidden (``scope_tenant_field`` behaviour).
    ``tom_select``
        Apply ``data-tom-select`` to eligible select widgets.

    Fields added to ``self.fields`` after ``super().__init__()`` returns are not
    rewritten; call ``apply_tenant_scoped_choices(self, ...)`` again to cover
    them.
    """

    tenant_scoped_choice_exclusions: tuple[str, ...] = ()
    tenant_required: bool = False
    tenant_autoset_when_single: bool = False
    tom_select: bool = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        fields = self.fields  # type: ignore[attr-defined]
        apply_tenant_scoped_choices(self, exclude=self.tenant_scoped_choice_exclusions)  # type: ignore[arg-type]
        if "tenant" in fields:
            self._apply_tenant_declarations()
        if self.tom_select:
            apply_tom_select(fields)

    def _apply_tenant_declarations(self) -> None:
        fields = self.fields  # type: ignore[attr-defined]
        if self.tenant_required and "tenant_group" not in fields and tenant_rows_exist():
            fields["tenant"].required = True
        if self.tenant_autoset_when_single:
            scope_tenant_field(self, "tenant")
