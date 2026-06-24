from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectBulkDeleteView, ObjectCloneView,
)
from ..models import TenantRole, TenantMembership
from ..forms import TenantRoleForm, TenantRoleFilterForm, TenantRoleAssignUsersForm
from ..tables import TenantRoleTable
from ..filters import TenantRoleFilterSet


class TenantRoleListView(ObjectListView):
    queryset = TenantRole.objects.annotate(member_count=Count('memberships', distinct=True))
    filterset = TenantRoleFilterSet
    filterset_form = TenantRoleFilterForm
    table = TenantRoleTable
    action_buttons = ('add',)


class TenantRoleDetailView(ObjectDetailView):
    queryset = TenantRole.objects.annotate(member_count=Count('memberships', distinct=True))
    template_name = 'organization/tenantroles/tenantrole_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from ..forms.tenantrole_form import MATRIX_MODELS
        groups = {}
        for key, info in MATRIX_MODELS.items():
            group_name = info.get('group', 'Other')
            if group_name not in groups:
                groups[group_name] = []
            app = info['app']
            model = info['model_name']
            groups[group_name].append({
                'label': info['label'],
                'read_codename': f'{app}.view_{model}',
                'create_codename': f'{app}.add_{model}',
                'edit_codename': f'{app}.change_{model}',
                'delete_codename': f'{app}.delete_{model}',
            })
        context['matrix_grouped_items'] = groups
        role = self.get_object()
        context['member_count'] = getattr(role, 'member_count', 0) or 0
        context['members_url'] = f"{reverse('organization:tenantmembership_list')}?role={role.pk}"
        return context


class TenantRoleEditView(ObjectEditView):
    queryset = TenantRole.objects.all()
    model = TenantRole
    model_form = TenantRoleForm
    template_name = 'organization/tenantrole_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['tenant'] = getattr(self.request, 'active_tenant', None)
        return kwargs


class TenantRoleCloneView(ObjectCloneView):
    """Clone a role's permission set into (potentially) a different tenant.

    Unlike the default clone flow, the new role is NOT persisted on GET:
    TenantRole.tenant is non-nullable, so we leave it blank and let the admin
    pick the target tenant on the form. This makes onboarding a new tenant with
    a similar permission set a single step.
    """
    model = TenantRole
    model_form = TenantRoleForm
    template_name = 'organization/tenantrole_form.html'

    def get_form_kwargs(self):
        # Pass `user` but intentionally NOT `tenant`: with no active tenant bound
        # and an unsaved instance, TenantRoleForm renders a required tenant
        # picker so the clone is assigned to a freshly chosen tenant.
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def pre_save_clone(self, original, cloned):
        # Clear the tenant so the admin must choose a target tenant on the form.
        cloned.tenant = None


class TenantRoleDeleteView(ObjectDeleteView):
    queryset = TenantRole.objects.all()
    model = TenantRole
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:tenantrole_list')


class TenantRoleBulkDeleteView(ObjectBulkDeleteView):
    queryset = TenantRole.objects.all()

    def post(self, request, *args, **kwargs):
        from django.db import transaction
        from itambox.views.generic.utils import safe_return_url
        from django.http import HttpResponseRedirect

        pks = request.POST.getlist('pk')
        model = self._get_model()
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('organization:tenantrole_list'),
        )

        if not pks:
            messages.warning(request, _("No roles were selected."))
            return HttpResponseRedirect(return_url)

        queryset = self._get_queryset(pks)
        objects_to_delete = list(queryset)

        if not objects_to_delete:
            messages.warning(request, _("No valid roles selected for deletion."))
            return HttpResponseRedirect(return_url)

        if '_confirm' in request.POST:
            try:
                deleted_count = 0
                with transaction.atomic():
                    for obj in objects_to_delete:
                        obj.delete()
                        deleted_count += 1
                messages.success(request, _("Successfully deleted %(count)d role(s).") % {'count': deleted_count})
                return HttpResponseRedirect(return_url)
            except ProtectedError as e:
                # Extract the names of the roles that still have memberships.
                blocked_names = ', '.join(str(o) for o in objects_to_delete if o.memberships.exists())
                messages.error(
                    request,
                    _("Cannot delete: the following roles still have members and are protected: %(names)s. "
                      "Remove all memberships from these roles before deleting them.") % {'names': blocked_names}
                )
                return HttpResponseRedirect(return_url)
        else:
            context = {
                'model': model,
                'model_name': f'{model._meta.app_label}.{model._meta.model_name}',
                'model_verbose_name': model._meta.verbose_name,
                'model_verbose_name_plural': model._meta.verbose_name_plural,
                'objects': objects_to_delete,
                'object_pks': pks,
                'return_url': return_url,
                'title': _('Confirm Bulk Deletion'),
                'breadcrumbs': [
                    (reverse('dashboard'), _('Dashboard')),
                    (return_url, _('Tenant Roles')),
                    (None, _('Delete (%(count)d)') % {'count': len(objects_to_delete)}),
                ],
            }
            return self.render_to_response(context)


class TenantRoleAssignUsersView(LoginRequiredMixin, View):
    # NOTE: TenantMembership has no ChangeLoggingMixin — these mutations are not change-logged.
    template_name = 'organization/tenantroles/tenantrole_assign_users.html'

    def _get_role(self, pk):
        return get_object_or_404(TenantRole, pk=pk)

    def _check_perms(self, request, role):
        return (
            request.user.has_perm('organization.add_tenantmembership', obj=role.tenant) and
            request.user.has_perm('organization.change_tenantmembership', obj=role.tenant)
        )

    def get(self, request, pk, *args, **kwargs):
        role = self._get_role(pk)
        if not self._check_perms(request, role):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = TenantRoleAssignUsersForm()
        return render(request, self.template_name, {'role': role, 'form': form})

    def post(self, request, pk, *args, **kwargs):
        role = self._get_role(pk)
        if not self._check_perms(request, role):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        form = TenantRoleAssignUsersForm(request.POST)
        if form.is_valid():
            users = form.cleaned_data['users']
            added = updated = unchanged = 0
            with transaction.atomic():
                for user in users:
                    existing = TenantMembership.objects.filter(user=user, tenant=role.tenant).first()
                    if existing is None:
                        membership = TenantMembership.objects.create(user=user, tenant=role.tenant)
                        membership.roles.add(role)
                        added += 1
                    elif not existing.roles.filter(pk=role.pk).exists():
                        existing.roles.add(role)
                        updated += 1
                    else:
                        unchanged += 1
            messages.success(
                request,
                _("Assigned '%(role)s': %(added)d added, %(updated)d updated, %(unchanged)d unchanged.") % {
                    'role': role.name,
                    'added': added,
                    'updated': updated,
                    'unchanged': unchanged,
                }
            )
            return redirect(reverse('organization:tenantrole_detail', kwargs={'pk': role.pk}))

        return render(request, self.template_name, {'role': role, 'form': form})
