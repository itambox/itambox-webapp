"""Views for the unified ``Membership`` model (thin anchor + RoleAssignment grants)."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy as _
from django.views import View

from core.auth.guards import validate_assignment_grant
from itambox.views.generic.utils import safe_return_url
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectBulkEditView, ObjectBulkDeleteView,
)
from ..access import tenant_access_report
from ..models import Membership, RoleAssignment, Tenant
from ..forms import MembershipForm, MembershipFilterForm, MembershipBulkRoleForm
from ..tables import MembershipTable
from ..filters import MembershipFilterSet
from ..services import visible_to_containers

User = get_user_model()


class MembershipListView(ObjectListView):
    # ``has_managed_reach`` feeds MembershipTable's Staff/Member badge as a single
    # correlated EXISTS in the list query — the model property would otherwise run
    # one exists() query per rendered row. (Membership/RoleAssignment default
    # managers are deliberately unscoped, so baking this at import time is safe.)
    queryset = (
        Membership.objects
        .select_related('user', 'tenant')
        .prefetch_related('assignments__role')
        .annotate(
            has_managed_reach=Exists(
                RoleAssignment.objects.filter(
                    membership_id=OuterRef('pk'),
                    reach=RoleAssignment.REACH_MANAGED,
                )
            )
        )
    )
    filterset = MembershipFilterSet
    filterset_form = MembershipFilterForm
    table = MembershipTable
    action_buttons = ('add',)

    def get(self, request, *args, **kwargs):
        # Lazy partial route (mirrors the ?tab= pattern on detail pages): the
        # members list embeds a container that hx-gets this same URL with
        # ?panel=outside_access on load. Keeping it on the list URL means the
        # panel inherits the list's LoginRequired/PermissionRequired gate.
        if request.GET.get('panel') == 'outside_access':
            return self._render_outside_access_panel(request)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        return visible_to_containers(self.request.user, qs, 'organization.view_membership')

    def _outside_access_tenant(self):
        """The active tenant when the requester may audit its access, else ``None``.

        Mirrors ``TenantAccessView``'s object-level check: the panel is an access
        audit for one specific tenant, so the ambient module permission alone
        (checked by ``PermissionRequiredMixin``) is not enough.
        """
        tenant = getattr(self.request, 'active_tenant', None)
        if tenant is None:
            return None
        if self.request.user.is_superuser or self.request.user.has_perm(
            'organization.view_membership', obj=tenant,
        ):
            return tenant
        return None

    def _render_outside_access_panel(self, request):
        tenant = self._outside_access_tenant()
        if tenant is None:
            return HttpResponse('')
        entries = tenant_access_report(tenant, external_only=True)
        # Empty report → empty response: the panel chrome only ever renders when
        # someone actually reaches this tenant from outside.
        if not entries:
            return HttpResponse('')
        return render(request, 'organization/memberships/outside_access_panel.html', {
            'tenant': tenant,
            'entries': entries,
        })

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['outside_access_tenant'] = self._outside_access_tenant()
        return context


class MembershipDetailView(ObjectDetailView):
    queryset = (
        Membership.objects
        .select_related('user', 'tenant')
        .prefetch_related(
            'assignments__role',
            'assignments__scope_group',
            'assignments__assigned_tenants',
            'assignments__granted_by',
        )
    )
    template_name = 'organization/memberships/membership_detail.html'

    def get_queryset(self):
        qs = super().get_queryset()
        return visible_to_containers(self.request.user, qs, 'organization.view_membership')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['assignments'] = self.object.assignments.all()
        return context


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
    manager of the membership's tenant manually send the standard Django password-reset
    email, which links to the ``password_reset_confirm`` route — usable both to reset a
    forgotten password and to set an initial one on a fresh ``set_unusable_password()``
    account.
    """
    http_method_names = ['post']

    def post(self, request, pk):
        membership = get_object_or_404(
            Membership.objects.select_related('user', 'tenant'), pk=pk,
        )
        detail_url = reverse('organization:membership_detail', kwargs={'pk': membership.pk})

        # Guard: only a manager of the membership's tenant (or a superuser) may
        # trigger credential issuance for its user.
        if not (
            request.user.is_superuser
            or request.user.has_perm('organization.change_membership', obj=membership.tenant)
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
    """The unified "Add member" flow: who / what (roles) / where (reach) in one form."""
    queryset = Membership.objects.all()
    model = Membership
    model_form = MembershipForm
    template_name = 'organization/memberships/membership_form.html'

    def get_initial(self):
        initial = super().get_initial()
        for key in ('user', 'tenant'):
            val = self.request.GET.get(key)
            if val:
                initial[key] = val
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        # An explicit ?tenant=<pk> deep-link (e.g. "add member" from a tenant page) wins
        # over the ambient active tenant.
        tenant = None
        tenant_param = self.request.GET.get('tenant')
        if tenant_param:
            try:
                tenant = Tenant._base_manager.filter(
                    pk=tenant_param, deleted_at__isnull=True,
                ).first()
            except (TypeError, ValueError):  # non-numeric ?tenant= must not 500
                tenant = None
        kwargs['tenant'] = tenant or getattr(self.request, 'active_tenant', None)
        # ?preset=technician (the redirected quick-onboard flow) preselects
        # who=new + managed reach/all + the shared Technician role.
        preset = self.request.GET.get('preset')
        if preset:
            kwargs['preset'] = preset
        return kwargs

    # Success redirect: the generic get_success_url falls back to
    # ``Membership.get_absolute_url()`` — the membership detail, where the new
    # assignments (and the send-password-setup action) live.

    def form_valid(self, form):
        response = super().form_valid(form)
        membership = self.object
        if membership is None:
            return response
        if getattr(form, 'new_user_created', False):
            # The inline-created user has an unusable password: point the admin
            # straight at the existing "Send password setup link" action.
            setup_url = reverse(
                'organization:membership_detail', kwargs={'pk': membership.pk},
            ) + '#send-password-setup'
            messages.info(self.request, format_html(
                gettext('{user} has no password yet — <a href="{url}">send them a '
                        'password setup link</a> so they can sign in.'),
                user=str(membership.user), url=setup_url,
            ))
        if not membership.assignments.exists():
            # Role-less onboarding is allowed (first hire), but the membership
            # carries zero permissions until a role is assigned — warn loudly.
            messages.warning(self.request, _(
                "%(user)s is now a member of %(tenant)s but has NO permissions "
                "yet — edit the membership to assign a role."
            ) % {'user': membership.user, 'tenant': membership.tenant})
        return response


class MembershipEditView(ObjectEditView):
    """Edit a membership's assignments / active flag. User + tenant are immutable.

    The form reconciles RoleAssignment rows at BOTH reaches (own + managed);
    success falls through to the generic redirect → membership detail.
    """
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
        for f in ('user', 'tenant'):
            if f in form.fields:
                form.fields[f].disabled = True
        return form


class MembershipDeleteView(ObjectDeleteView):
    queryset = Membership.objects.all()
    model = Membership
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        mem = self.get_object()
        return reverse('users:user_detail', kwargs={'pk': mem.user.pk})


class MembershipBulkEditView(ObjectBulkEditView):
    """Bulk role (re-)assignment for memberships sharing one tenant.

    Adds/removes own-reach ``RoleAssignment`` rows; every added role passes
    :func:`validate_assignment_grant` before anything is written.
    """
    queryset = Membership.objects.all()
    form_class = MembershipBulkRoleForm

    def _get_queryset(self, pks):
        qs = Membership.objects.filter(pk__in=pks)
        return visible_to_containers(self.request.user, qs, 'organization.change_membership')

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
        objects = list(queryset.select_related('tenant'))
        if not objects:
            messages.warning(request, _("No valid memberships selected."))
            return HttpResponseRedirect(return_url)

        if '_apply' in request.POST:
            form = MembershipBulkRoleForm(request.POST)
            if form.is_valid():
                roles_to_add = list(form.cleaned_data.get('roles_to_add') or [])
                roles_to_remove = list(form.cleaned_data.get('roles_to_remove') or [])
                if not roles_to_add and not roles_to_remove:
                    messages.warning(request, _("No roles to add or remove were specified."))
                    return HttpResponseRedirect(return_url)

                # Memberships must share one tenant; roles must be assignable there
                # (owned by the tenant, or shared down by its managing tenant).
                tenant_pks = {m.tenant_id for m in objects}
                if len(tenant_pks) != 1:
                    messages.error(
                        request,
                        _("Cannot bulk reassign: selected memberships span multiple tenants."),
                    )
                    return HttpResponseRedirect(return_url)

                tenant = objects[0].tenant
                for role in roles_to_add + roles_to_remove:
                    assignable = role.tenant_id == tenant.pk or (
                        tenant.managed_by_id
                        and role.tenant_id == tenant.managed_by_id
                        and role.shared_with_managed
                    )
                    if not assignable:
                        messages.error(
                            request,
                            _("Role '%(role)s' is not assignable in this tenant.") % {'role': role},
                        )
                        return HttpResponseRedirect(return_url)

                if not request.user.has_perm('organization.change_membership', obj=tenant):
                    messages.error(request, _("You do not have permission to change memberships here."))
                    return HttpResponseRedirect(return_url)

                # Privilege-escalation guard, per role being granted.
                try:
                    for role in roles_to_add:
                        validate_assignment_grant(
                            request.user, role, tenant, reach=RoleAssignment.REACH_OWN,
                        )
                except ValidationError as e:
                    messages.error(request, ", ".join(e.messages))
                    return HttpResponseRedirect(return_url)

                with transaction.atomic():
                    for obj in objects:
                        for role in roles_to_add:
                            RoleAssignment.objects.get_or_create(
                                membership=obj, role=role, reach=RoleAssignment.REACH_OWN,
                                defaults={'granted_by': request.user},
                            )
                        if roles_to_remove:
                            # Per-instance delete so each revoke hits the changelog
                            # (queryset.delete() would bypass ChangeLoggingMixin).
                            stale = RoleAssignment.objects.filter(
                                membership=obj,
                                role__in=roles_to_remove,
                                reach=RoleAssignment.REACH_OWN,
                            )
                            for assignment in stale:
                                assignment.delete()
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
        return visible_to_containers(self.request.user, qs, 'organization.delete_membership')
