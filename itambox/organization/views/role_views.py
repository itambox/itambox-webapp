"""Views for the unified ``Role`` model."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectBulkDeleteView, ObjectCloneView,
)
from itambox.views.generic.utils import safe_return_url

from ..models import Role, Membership, Provider, Tenant
from ..forms import RoleForm, RoleFilterForm, RoleAssignUsersForm
from ..tables import RoleTable
from ..filters import RoleFilterSet


class RoleListView(ObjectListView):
    # select_related the two FKs RoleTable.render_container dereferences via Role.owner
    # (tenant XOR provider) -- avoids an N+1 query per row on the list page.
    queryset = Role.objects.annotate(
        member_count=Count('memberships', distinct=True)
    ).select_related('tenant', 'provider')
    filterset = RoleFilterSet
    filterset_form = RoleFilterForm
    table = RoleTable
    action_buttons = ('add',)


class RoleDetailView(ObjectDetailView):
    queryset = Role.objects.annotate(member_count=Count('memberships', distinct=True))
    template_name = 'organization/roles/role_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = self.get_object()
        from ..forms.role_form import MATRIX_MODELS, PROVIDER_CAPABILITIES, CUSTOM_PERMISSIONS
        groups = {}
        for key, info in MATRIX_MODELS.items():
            app, model = info['app'], info['model_name']
            groups.setdefault(info.get('group', 'Other'), []).append({
                'label': info['label'],
                'read_codename': f'{app}.view_{model}',
                'create_codename': f'{app}.add_{model}',
                'edit_codename': f'{app}.change_{model}',
                'delete_codename': f'{app}.delete_{model}',
            })
        context['matrix_grouped_items'] = groups
        context['custom_permissions'] = CUSTOM_PERMISSIONS
        context['provider_capabilities'] = PROVIDER_CAPABILITIES
        context['member_count'] = getattr(role, 'member_count', 0) or 0
        context['members_url'] = f"{reverse('organization:membership_list')}?role={role.pk}"
        return context


class RoleEditView(ObjectEditView):
    queryset = Role.objects.all()
    model = Role
    model_form = RoleForm
    template_name = 'organization/role_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        is_create = self.kwargs.get('pk') is None
        # A container deep-link pre-binds the new role's scope. ?provider=<pk> (e.g. role-less
        # technician onboarding) makes a provider role; ?tenant=<pk> makes a tenant role. On
        # edit the form locks to the instance's own container, so these are create-only.
        if is_create:
            provider_id = self.request.GET.get('provider')
            if provider_id:
                provider = Provider._base_manager.filter(
                    pk=provider_id, deleted_at__isnull=True,
                ).first()
                if provider is not None:
                    kwargs['provider'] = provider
                    return kwargs
            tenant_id = self.request.GET.get('tenant')
            if tenant_id:
                tenant = Tenant._base_manager.filter(
                    pk=tenant_id, deleted_at__isnull=True,
                ).first()
                if tenant is not None:
                    kwargs['tenant'] = tenant
                    return kwargs
            # No container context on a fresh add → present the tenant-vs-provider chooser so
            # the user selects the role's scope explicitly (rather than being forced onto the
            # active tenant, which made provider roles uncreatable from the UI).
            kwargs['allow_container_choice'] = True
        return kwargs


class RoleCloneView(ObjectCloneView):
    """Clone a role's permission set, optionally retargeting tenant/provider."""
    model = Role
    model_form = RoleForm
    template_name = 'organization/role_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def pre_save_clone(self, original, cloned):
        # Clear container so the admin picks the target on the form.
        cloned.tenant = None
        cloned.provider = None


class RoleDeleteView(ObjectDeleteView):
    queryset = Role.objects.all()
    model = Role
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:role_list')


class RoleBulkDeleteView(ObjectBulkDeleteView):
    queryset = Role.objects.all()

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        model = self._get_model()
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('organization:role_list'),
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
                count = 0
                with transaction.atomic():
                    for obj in objects_to_delete:
                        obj.delete()
                        count += 1
                messages.success(request, _("Deleted %(count)d role(s).") % {'count': count})
                return HttpResponseRedirect(return_url)
            except ProtectedError:
                blocked = ', '.join(str(o) for o in objects_to_delete if o.memberships.exists())
                messages.error(
                    request,
                    _("Cannot delete: the following roles still have members and are protected: %(names)s.") % {'names': blocked},
                )
                return HttpResponseRedirect(return_url)

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
                (return_url, _('Roles')),
                (None, _('Delete (%(count)d)') % {'count': len(objects_to_delete)}),
            ],
        }
        return self.render_to_response(context)


class RoleAssignUsersView(LoginRequiredMixin, View):
    """Bulk-add users to a Role (creates Memberships as needed)."""
    template_name = 'organization/roles/role_assign_users.html'

    def _get_role(self, pk):
        return get_object_or_404(Role, pk=pk)

    def _check_perms(self, request, role):
        # Use the role's container for the permission check.
        container = role.owner
        return (
            request.user.has_perm('organization.add_membership', obj=container) and
            request.user.has_perm('organization.change_membership', obj=container)
        )

    def get(self, request, pk, *args, **kwargs):
        role = self._get_role(pk)
        if not self._check_perms(request, role):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        # Check privilege-escalation guard
        from core.auth.guards import validate_permission_grant
        from django.core.exceptions import PermissionDenied, ValidationError
        try:
            validate_permission_grant(request.user, role.permissions or [], role.owner)
        except ValidationError as e:
            raise PermissionDenied(e.message)

        return render(request, self.template_name, {'role': role, 'form': RoleAssignUsersForm()})

    def post(self, request, pk, *args, **kwargs):
        role = self._get_role(pk)
        if not self._check_perms(request, role):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        # Check privilege-escalation guard
        from core.auth.guards import validate_permission_grant
        from django.core.exceptions import PermissionDenied, ValidationError
        try:
            validate_permission_grant(request.user, role.permissions or [], role.owner)
        except ValidationError as e:
            raise PermissionDenied(e.message)

        form = RoleAssignUsersForm(request.POST)
        if form.is_valid():
            users = form.cleaned_data['users']
            added = updated = unchanged = 0
            with transaction.atomic():
                for user in users:
                    if role.scope == Role.SCOPE_TENANT:
                        existing = Membership.objects.filter(user=user, tenant=role.tenant).first()
                        if existing is None:
                            mem = Membership.objects.create(
                                user=user, tenant=role.tenant,
                            )
                            mem.roles.add(role)
                            added += 1
                            continue
                    else:
                        existing = Membership.objects.filter(
                            user=user, provider=role.provider,
                        ).first()
                        if existing is None:
                            mem = Membership.objects.create(
                                user=user, provider=role.provider,
                                tenant_scope=Membership.SCOPE_EXPLICIT,
                            )
                            mem.roles.add(role)
                            added += 1
                            continue
                    if existing.roles.filter(pk=role.pk).exists():
                        unchanged += 1
                    else:
                        existing.roles.add(role)
                        updated += 1
            messages.success(
                request,
                _("Assigned '%(role)s': %(added)d added, %(updated)d updated, %(unchanged)d unchanged.") % {
                    'role': role.name, 'added': added, 'updated': updated, 'unchanged': unchanged,
                },
            )
            return redirect(reverse('organization:role_detail', kwargs={'pk': role.pk}))
        return render(request, self.template_name, {'role': role, 'form': form})
