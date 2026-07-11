"""Views for the unified ``Role`` model (tenant-owned, optionally shared down)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

from core.auth.guards import validate_assignment_grant
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectBulkDeleteView, ObjectCloneView,
)
from itambox.views.generic.utils import safe_return_url

from ..models import Role, Membership, RoleAssignment, Tenant
from ..forms import RoleForm, RoleFilterForm, RoleAssignUsersForm
from ..forms.role_form import MATRIX_MODELS, CUSTOM_PERMISSIONS
from ..tables import RoleTable
from ..filters import RoleFilterSet


def _roles_visible_in(tenant):
    """Roles assignable/visible inside ``tenant``: its own plus the ones its
    managing tenant shares down. ``_base_manager`` because the shared half lives
    outside the active tenant's scope."""
    q = Q(tenant=tenant)
    if tenant.managed_by_id:
        q |= Q(tenant_id=tenant.managed_by_id, shared_with_managed=True)
    return Role._base_manager.filter(deleted_at__isnull=True).filter(q)


def _annotate_member_count(qs):
    return qs.annotate(member_count=Count('assignments__membership', distinct=True))


class RoleListView(ObjectListView):
    queryset = _annotate_member_count(Role.objects.all()).select_related('tenant')
    filterset = RoleFilterSet
    filterset_form = RoleFilterForm
    table = RoleTable
    action_buttons = ('add',)

    def get_queryset(self):
        # Active tenant's own roles ∪ roles its managing tenant shares down.
        # Shared roles are listed but only editable by their owner (RoleEditView
        # resolves through the tenant-scoped manager, so they 404 on edit).
        tenant = getattr(self.request, 'active_tenant', None)
        if tenant is not None:
            self.queryset = _annotate_member_count(
                _roles_visible_in(tenant)
            ).select_related('tenant')
        return super().get_queryset()


class RoleDetailView(ObjectDetailView):
    queryset = _annotate_member_count(Role.objects.all())
    template_name = 'organization/roles/role_detail.html'

    def get_queryset(self):
        # A managed tenant may view (read-only) the roles shared down to it.
        tenant = getattr(self.request, 'active_tenant', None)
        if tenant is not None:
            self.queryset = _annotate_member_count(_roles_visible_in(tenant))
        return super().get_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = self.get_object()
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
        context['member_count'] = getattr(role, 'member_count', 0) or 0
        context['members_url'] = f"{reverse('organization:membership_list')}?role={role.pk}"
        # A role shared down by a managing tenant is read-only here.
        active = getattr(self.request, 'active_tenant', None)
        context['role_editable'] = bool(active is not None and role.tenant_id == active.pk)
        return context


class RoleEditView(ObjectEditView):
    queryset = Role.objects.all()
    model = Role
    model_form = RoleForm
    template_name = 'organization/role_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        # A ?tenant=<pk> deep-link pre-binds the new role's owner (e.g. role-less
        # technician onboarding). On edit the form locks to the instance's own
        # tenant, so this is create-only.
        if self.kwargs.get('pk') is None:
            tenant_id = self.request.GET.get('tenant')
            if tenant_id:
                try:
                    tenant = Tenant._base_manager.filter(
                        pk=tenant_id, deleted_at__isnull=True,
                    ).first()
                except (TypeError, ValueError):  # non-numeric ?tenant= must not 500
                    tenant = None
                if tenant is not None:
                    kwargs['tenant'] = tenant
        return kwargs


class RoleCloneView(ObjectCloneView):
    """Clone a role's permission set; the form re-binds the owner tenant."""
    model = Role
    model_form = RoleForm
    template_name = 'organization/role_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def pre_save_clone(self, original, cloned):
        # Clear the owner so the clone binds to the admin's context tenant on save
        # (a shared role cloned from a managed tenant must not stay provider-owned).
        cloned.tenant = None


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
                blocked = ', '.join(str(o) for o in objects_to_delete if o.assignments.exists())
                messages.error(
                    request,
                    _("Cannot delete: the following roles are still referenced and are protected: %(names)s.") % {'names': blocked},
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
    """Bulk-add users to a Role: get_or_create a Membership at the role's tenant
    plus an own-reach RoleAssignment per user."""
    template_name = 'organization/roles/role_assign_users.html'

    def _get_role(self, pk):
        return get_object_or_404(Role, pk=pk)

    def _check_perms(self, request, role):
        # Granting happens at the role's owning tenant.
        tenant = role.tenant
        return (
            request.user.has_perm('organization.add_membership', obj=tenant) and
            request.user.has_perm('organization.change_membership', obj=tenant)
        )

    def _guard_grant(self, request, role):
        """Privilege-escalation guard for granting this role at its own tenant."""
        try:
            validate_assignment_grant(
                request.user, role, role.tenant, reach=RoleAssignment.REACH_OWN,
            )
        except ValidationError as e:
            raise PermissionDenied(", ".join(e.messages))

    def get(self, request, pk, *args, **kwargs):
        role = self._get_role(pk)
        if not self._check_perms(request, role):
            raise PermissionDenied
        self._guard_grant(request, role)
        return render(request, self.template_name, {'role': role, 'form': RoleAssignUsersForm()})

    def post(self, request, pk, *args, **kwargs):
        role = self._get_role(pk)
        if not self._check_perms(request, role):
            raise PermissionDenied
        self._guard_grant(request, role)

        form = RoleAssignUsersForm(request.POST)
        if form.is_valid():
            users = form.cleaned_data['users']
            added = updated = unchanged = 0
            with transaction.atomic():
                for user in users:
                    membership, membership_created = Membership.objects.get_or_create(
                        user=user, tenant=role.tenant,
                        defaults={'is_active': True},
                    )
                    _assignment, assignment_created = RoleAssignment.objects.get_or_create(
                        membership=membership, role=role, reach=RoleAssignment.REACH_OWN,
                        defaults={'granted_by': request.user},
                    )
                    if membership_created:
                        added += 1
                    elif assignment_created:
                        updated += 1
                    else:
                        unchanged += 1
            messages.success(
                request,
                _("Assigned '%(role)s': %(added)d added, %(updated)d updated, %(unchanged)d unchanged.") % {
                    'role': role.name, 'added': added, 'updated': updated, 'unchanged': unchanged,
                },
            )
            return redirect(reverse('organization:role_detail', kwargs={'pk': role.pk}))
        return render(request, self.template_name, {'role': role, 'form': form})
