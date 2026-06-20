"""Owning-tenant picker scoping for ModelForms.

A form's ``tenant`` ``ModelChoiceField`` defaults to an import-frozen
``Tenant.objects.all()`` queryset, which lists *every* tenant's name to any
member who can open the form (a low-severity cross-tenant disclosure) and is
needless UX clutter. ``scope_tenant_field`` fixes both, accounting for tenant
groups.
"""
from django import forms


def scope_tenant_field(form, field_name='tenant', autoset_when_single=True):
    """Scope a form's owning-tenant picker to the tenants the user may use.

    - **Superusers / system context** — left untouched (full picker, for
      MSP/operator flexibility).
    - **Non-superusers** — the queryset is restricted to the tenants accessible
      in the current active context: the active tenant, or — in tenant-group
      mode — the group's descendant tenants the user is a member of. That is
      exactly what ``Tenant.objects.all()`` returns through the tenant-scoping
      manager, so a member of ``tenant-a1`` and ``tenant-a2`` (active on their
      group) sees both, and only those.

      When ``autoset_when_single`` and exactly one tenant is accessible, the
      field is set to it and hidden (single-tenant members never see a pointless
      one-option dropdown). With several accessible tenants (group mode) a scoped
      picker is shown so the member chooses.

    Reads the active context from the contextvars the middleware already sets, so
    no ``request`` has to be threaded into the form. The view-layer permission
    check in ``ObjectEditView.form_valid`` remains as defence-in-depth.
    """
    field = form.fields.get(field_name)
    if field is None:
        return

    # inline imports: avoid a core.forms -> middleware/models import cycle at load
    from itambox.middleware import get_current_user
    user = get_current_user()
    if user is None or getattr(user, 'is_superuser', False):
        return  # operator / system context keeps the full picker

    from organization.models import Tenant
    accessible = Tenant.objects.all()  # tenant-scoping manager → accessible set
    field.queryset = accessible

    if not autoset_when_single:
        return

    accessible_pks = list(accessible.values_list('pk', flat=True))
    if len(accessible_pks) == 1:
        field.disabled = True
        field.required = False
        field.widget = forms.HiddenInput()
        instance = getattr(form, 'instance', None)
        if not (instance and getattr(instance, f'{field_name}_id', None)):
            form.initial[field_name] = accessible_pks[0]
