from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy

from itambox.views.generic.utils import safe_return_url
from itambox.views.generic import (
    ObjectListView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from ..models import TenantMembership, Tenant
from ..forms import TenantMembershipForm, TenantMembershipFilterForm, TenantMembershipBulkRoleForm
from ..tables import TenantMembershipTable
from ..filters import TenantMembershipFilterSet


class TenantMembershipListView(ObjectListView):
    queryset = TenantMembership.objects.select_related('user', 'tenant', 'role')
    filterset = TenantMembershipFilterSet
    filterset_form = TenantMembershipFilterForm
    table = TenantMembershipTable
    action_buttons = ('add',)


class TenantMembershipCreateView(ObjectEditView):
    queryset = TenantMembership.objects.all()
    model = TenantMembership
    model_form = TenantMembershipForm
    template_name = 'generic/object_edit.html'

    def get_initial(self):
        initial = super().get_initial()
        user_pk = self.request.GET.get('user')
        if user_pk:
            initial['user'] = user_pk
        tenant_pk = self.request.GET.get('tenant')
        if tenant_pk:
            initial['tenant'] = tenant_pk
        return initial

    def get_success_url(self):
        if self.instance and self.instance.user:
            return reverse('users:user_detail', kwargs={'pk': self.instance.user.pk})
        user_pk = self.request.GET.get('user')
        if user_pk:
            return reverse('users:user_detail', kwargs={'pk': user_pk})
        return reverse('users:user_list')


class TenantMembershipEditView(ObjectEditView):
    """Edit a membership's role only — user and tenant are immutable."""
    queryset = TenantMembership.objects.all()
    model = TenantMembership
    model_form = TenantMembershipForm
    template_name = 'generic/object_edit.html'

    def get_form(self, data=None, files=None, **kwargs):
        form = super().get_form(data=data, files=files, **kwargs)
        # Disable user and tenant — only role is editable on an existing membership.
        form.fields['user'].disabled = True
        form.fields['tenant'].disabled = True
        return form

    def get_success_url(self):
        if self.instance and self.instance.user:
            return reverse('users:user_detail', kwargs={'pk': self.instance.user.pk})
        return reverse('organization:tenantmembership_list')


class TenantMembershipDeleteView(ObjectDeleteView):
    queryset = TenantMembership.objects.all()
    model = TenantMembership
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        membership = self.get_object()
        return reverse('users:user_detail', kwargs={'pk': membership.user.pk})


class TenantMembershipBulkEditView(ObjectBulkEditView):
    """Bulk role reassignment for memberships.

    Overrides _get_queryset to scope PKs to tenants the requesting user can
    administer — prevents pk-smuggling across tenant boundaries.
    """
    queryset = TenantMembership.objects.all()
    form_class = TenantMembershipBulkRoleForm

    def _get_queryset(self, pks):
        qs = TenantMembership.objects.filter(pk__in=pks)
        allowed_tenant_pks = [
            t.pk for t in Tenant.objects.all()
            if self.request.user.has_perm('organization.change_tenantmembership', obj=t)
        ]
        return qs.filter(tenant__in=allowed_tenant_pks)

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('organization:tenantmembership_list'),
        )

        if not pks:
            messages.warning(request, "No memberships were selected.")
            return HttpResponseRedirect(return_url)

        queryset = self._get_queryset(pks)
        objects = list(queryset)

        if not objects:
            messages.warning(request, "No valid memberships selected (you may lack permission for the selected tenants).")
            return HttpResponseRedirect(return_url)

        if '_apply' in request.POST:
            form = TenantMembershipBulkRoleForm(request.POST)
            if form.is_valid():
                new_role = form.cleaned_data['role']

                # All memberships must share a single tenant and it must match the new role's tenant.
                tenant_pks = {m.tenant_id for m in objects}
                if len(tenant_pks) > 1:
                    messages.error(
                        request,
                        "Cannot bulk reassign: selected memberships span multiple tenants. "
                        "Filter to a single tenant and try again."
                    )
                    return HttpResponseRedirect(return_url)

                membership_tenant = objects[0].tenant
                if new_role.tenant_id != membership_tenant.pk:
                    messages.error(
                        request,
                        f"Role '{new_role}' belongs to a different tenant than the selected memberships."
                    )
                    return HttpResponseRedirect(return_url)

                if not request.user.has_perm('organization.change_tenantmembership', obj=membership_tenant):
                    messages.error(request, "You do not have permission to change memberships for this tenant.")
                    return HttpResponseRedirect(return_url)

                updated_count = 0
                with transaction.atomic():
                    for obj in objects:
                        obj.role = new_role
                        obj.save()
                        updated_count += 1

                messages.success(request, f"Reassigned {updated_count} membership(s) to role '{new_role.name}'.")
                return HttpResponseRedirect(return_url)
        else:
            form = TenantMembershipBulkRoleForm()

        model = TenantMembership
        context = {
            'form': form,
            'model': model,
            'model_name': 'organization.tenantmembership',
            'objects': objects,
            'object_pks': pks,
            'return_url': return_url,
            'selected_fields': ['role'],
            'verbose_name': model._meta.verbose_name,
            'verbose_name_plural': model._meta.verbose_name_plural,
            'title': 'Bulk Reassign Role',
            'breadcrumbs': [
                (reverse('dashboard'), 'Dashboard'),
                (return_url, 'Memberships'),
                (None, f'Bulk Edit ({len(pks)})'),
            ],
        }
        return self.render_to_response(context)


class TenantMembershipBulkDeleteView(ObjectBulkDeleteView):
    """Bulk delete memberships.

    Scopes the PK list to tenants the requesting user can administer,
    preventing cross-tenant deletion via crafted POSTs.
    """
    queryset = TenantMembership.objects.all()

    def _get_queryset(self, pks):
        qs = TenantMembership.objects.filter(pk__in=pks)
        allowed_tenant_pks = [
            t.pk for t in Tenant.objects.all()
            if self.request.user.has_perm('organization.delete_tenantmembership', obj=t)
        ]
        return qs.filter(tenant__in=allowed_tenant_pks)
