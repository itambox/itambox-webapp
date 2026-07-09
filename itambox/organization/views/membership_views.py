"""Views for the unified ``Membership`` model."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View

from itambox.views.generic.utils import safe_return_url
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectBulkEditView, ObjectBulkDeleteView,
)
from ..models import Membership, Tenant, Provider
from ..forms import MembershipForm, MembershipFilterForm, MembershipBulkRoleForm
from ..tables import MembershipTable
from ..filters import MembershipFilterSet

User = get_user_model()


class MembershipListView(ObjectListView):
    queryset = (
        Membership.objects
        .select_related('user', 'tenant', 'provider', 'scope_group')
        .prefetch_related('roles')
    )
    filterset = MembershipFilterSet
    filterset_form = MembershipFilterForm
    table = MembershipTable
    action_buttons = ('add',)


class MembershipDetailView(ObjectDetailView):
    queryset = (
        Membership.objects
        .select_related('user', 'tenant', 'provider', 'scope_group')
        .prefetch_related('roles', 'assigned_tenants')
    )
    template_name = 'organization/memberships/membership_detail.html'


class _SetInitialOrResetPasswordForm(PasswordResetForm):
    """``PasswordResetForm`` that also serves accounts with an unusable password.

    Stock ``PasswordResetForm.get_users()`` drops users whose password is unusable — but
    onboarding creates staff with ``set_unusable_password()``, so the stock form would
    silently email nobody. Onboarded staff are exactly who this action must reach (to set an
    initial password), so include unusable-password users; keep the ``is_active`` filter.
    """

    def get_users(self, email):
        email_field_name = User.get_email_field_name()
        active_users = User._default_manager.filter(
            **{f'{email_field_name}__iexact': email, 'is_active': True},
        )
        return (u for u in active_users if getattr(u, email_field_name))


class MembershipSendResetView(LoginRequiredMixin, View):
    """POST-only action: email a password-reset / set-password link to a membership's user.

    Onboarding creates staff with ``set_unusable_password()`` and no automatic credential
    issuance (the misleading ``send_invite`` checkbox was removed). This action lets a
    manager of the membership's container manually send the standard Django password-reset
    email, which links to the ``password_reset_confirm`` route — usable both to reset a
    forgotten password and to set an initial one on a fresh ``set_unusable_password()``
    account.
    """
    http_method_names = ['post']

    def post(self, request, pk):
        membership = get_object_or_404(
            Membership.objects.select_related('user', 'tenant', 'provider'), pk=pk,
        )
        detail_url = reverse('organization:membership_detail', kwargs={'pk': membership.pk})

        # Guard: only a manager of the membership's container (or a superuser) may
        # trigger credential issuance for its user.
        container = membership.container
        if not (
            request.user.is_superuser
            or (container is not None
                and request.user.has_perm('organization.change_membership', obj=container))
        ):
            messages.error(
                request,
                _("You do not have permission to send a password-reset link for this membership."),
            )
            return redirect(detail_url)

        user = membership.user
        email = (getattr(user, 'email', '') or '').strip()
        if not email:
            messages.error(
                request,
                _("This user has no email address, so no reset link can be sent."),
            )
            return redirect(detail_url)

        # Generate the token + email the standard reset link (to the ``password_reset_confirm``
        # route). We use a PasswordResetForm subclass that also serves unusable-password
        # accounts, since freshly-onboarded staff have no usable password yet.
        reset_form = _SetInitialOrResetPasswordForm(data={'email': email})
        if reset_form.is_valid():
            reset_form.save(
                request=request,
                use_https=request.is_secure(),
                from_email=None,
                email_template_name='registration/password_reset_email.html',
                subject_template_name='registration/password_reset_subject.txt',
            )
            messages.success(
                request,
                _("Sent a password-reset link to %(email)s.") % {'email': email},
            )
        else:
            messages.error(request, _("Could not send a reset link to %(email)s.") % {'email': email})
        return redirect(detail_url)


class MembershipCreateView(ObjectEditView):
    queryset = Membership.objects.all()
    model = Membership
    model_form = MembershipForm
    template_name = 'organization/memberships/membership_form.html'

    def get_initial(self):
        initial = super().get_initial()
        for key in ('user', 'tenant', 'provider'):
            val = self.request.GET.get(key)
            if val:
                initial[key] = val
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['tenant'] = getattr(self.request, 'active_tenant', None)
        return kwargs

    def get_success_url(self):
        if self.object and self.object.user:
            return reverse('users:user_detail', kwargs={'pk': self.object.user.pk})
        return reverse('organization:membership_list')


class MembershipEditView(ObjectEditView):
    """Edit a membership's roles / scope. User + container are immutable."""
    queryset = Membership.objects.all()
    model = Membership
    model_form = MembershipForm
    template_name = 'organization/memberships/membership_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class=form_class)
        for f in ('user', 'tenant', 'provider'):
            if f in form.fields:
                form.fields[f].disabled = True
        return form

    def get_success_url(self):
        if self.object and self.object.user:
            return reverse('users:user_detail', kwargs={'pk': self.object.user.pk})
        return reverse('organization:membership_list')


class MembershipDeleteView(ObjectDeleteView):
    queryset = Membership.objects.all()
    model = Membership
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        mem = self.get_object()
        return reverse('users:user_detail', kwargs={'pk': mem.user.pk})


class MembershipBulkEditView(ObjectBulkEditView):
    """Bulk role reassignment for memberships sharing one container."""
    queryset = Membership.objects.all()
    form_class = MembershipBulkRoleForm

    def _allowed_pks(self):
        # Tenant-membership PKs the user can administer.
        allowed_tenants = [
            t.pk for t in Tenant._base_manager.filter(deleted_at__isnull=True)
            if self.request.user.has_perm('organization.change_membership', obj=t)
        ]
        allowed_providers = [
            p.pk for p in Provider._base_manager.filter(deleted_at__isnull=True)
            if self.request.user.has_perm('organization.change_membership', obj=p)
        ]
        return allowed_tenants, allowed_providers

    def _get_queryset(self, pks):
        qs = Membership.objects.filter(pk__in=pks)
        allowed_tenants, allowed_providers = self._allowed_pks()
        from django.db.models import Q
        return qs.filter(Q(tenant_id__in=allowed_tenants) | Q(provider_id__in=allowed_providers))

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        return_url = safe_return_url(
            request,
            request.POST.get('return_url') or request.META.get('HTTP_REFERER'),
            reverse('organization:membership_list'),
        )
        if not pks:
            messages.warning(request, _("No memberships were selected."))
            return HttpResponseRedirect(return_url)
        queryset = self._get_queryset(pks)
        objects = list(queryset)
        if not objects:
            messages.warning(request, _("No valid memberships selected."))
            return HttpResponseRedirect(return_url)

        if '_apply' in request.POST:
            form = MembershipBulkRoleForm(request.POST)
            if form.is_valid():
                roles_to_add = form.cleaned_data.get('roles_to_add') or []
                roles_to_remove = form.cleaned_data.get('roles_to_remove') or []
                if not roles_to_add and not roles_to_remove:
                    messages.warning(request, _("No roles to add or remove were specified."))
                    return HttpResponseRedirect(return_url)

                # Memberships must share one container; roles must match.
                tenant_pks = {m.tenant_id for m in objects if m.tenant_id}
                provider_pks = {m.provider_id for m in objects if m.provider_id}
                if (len(tenant_pks) + len(provider_pks)) != 1:
                    messages.error(
                        request,
                        _("Cannot bulk reassign: selected memberships span multiple tenants/providers."),
                    )
                    return HttpResponseRedirect(return_url)

                container_tenant = objects[0].tenant
                container_provider = objects[0].provider
                container = container_provider or container_tenant
                for role in list(roles_to_add) + list(roles_to_remove):
                    if container_tenant and (role.tenant_id != container_tenant.pk):
                        messages.error(request, _("Role '%(role)s' does not belong to this tenant.") % {'role': role})
                        return HttpResponseRedirect(return_url)
                    if container_provider and (role.provider_id != container_provider.pk):
                        messages.error(request, _("Role '%(role)s' does not belong to this provider.") % {'role': role})
                        return HttpResponseRedirect(return_url)

                if not request.user.has_perm('organization.change_membership', obj=container):
                    messages.error(request, _("You do not have permission to change memberships here."))
                    return HttpResponseRedirect(return_url)

                # Check privilege-escalation guard on the roles being added
                if roles_to_add:
                    from core.auth.guards import validate_permission_grant
                    from django.core.exceptions import ValidationError
                    perms_to_add = set()
                    for r in roles_to_add:
                        perms_to_add.update(r.permissions or [])
                    try:
                        validate_permission_grant(request.user, perms_to_add, container)
                    except ValidationError as e:
                        messages.error(request, ", ".join(e.messages))
                        return HttpResponseRedirect(return_url)

                with transaction.atomic():
                    for obj in objects:
                        if roles_to_add:
                            obj.roles.add(*roles_to_add)
                        if roles_to_remove:
                            obj.roles.remove(*roles_to_remove)
                messages.success(request, _("Updated %(count)d membership(s).") % {'count': len(objects)})
                return HttpResponseRedirect(return_url)
        else:
            form = MembershipBulkRoleForm()

        model = Membership
        context = {
            'form': form,
            'model': model,
            'model_name': 'organization.membership',
            'objects': objects,
            'object_pks': pks,
            'return_url': return_url,
            'selected_fields': ['roles'],
            'verbose_name': model._meta.verbose_name,
            'verbose_name_plural': model._meta.verbose_name_plural,
            'title': _('Bulk Edit Roles'),
            'breadcrumbs': [
                (reverse('dashboard'), _('Dashboard')),
                (return_url, _('Memberships')),
                (None, _('Bulk Edit (%(count)d)') % {'count': len(pks)}),
            ],
        }
        return self.render_to_response(context)


class MembershipBulkDeleteView(ObjectBulkDeleteView):
    queryset = Membership.objects.all()

    def _get_queryset(self, pks):
        qs = Membership.objects.filter(pk__in=pks)
        allowed_tenants = [
            t.pk for t in Tenant._base_manager.filter(deleted_at__isnull=True)
            if self.request.user.has_perm('organization.delete_membership', obj=t)
        ]
        allowed_providers = [
            p.pk for p in Provider._base_manager.filter(deleted_at__isnull=True)
            if self.request.user.has_perm('organization.delete_membership', obj=p)
        ]
        from django.db.models import Q
        return qs.filter(Q(tenant_id__in=allowed_tenants) | Q(provider_id__in=allowed_providers))
