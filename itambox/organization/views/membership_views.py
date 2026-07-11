"""Views for the unified ``Membership`` model (thin anchor + RoleAssignment grants)."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch
from django.http import Http404, HttpResponse, HttpResponseRedirect
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
        # Join role + role.tenant into the assignment prefetch: the roles column
        # (and Role.__str__) touch role.tenant per row, which the tenant-scoped
        # Tenant manager would otherwise re-fetch one-.get()-per-row (an N+1 that
        # also fails closed outside the role's tenant context). select_related uses
        # a JOIN, so it is both constant-cost and scope-independent.
        .prefetch_related(
            Prefetch(
                'assignments',
                queryset=RoleAssignment.objects.select_related('role', 'role__tenant'),
            )
        )
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
        user = self.request.user
        active_tenant = getattr(self.request, 'active_tenant', None)
        active_group = getattr(self.request, 'active_tenant_group', None)
        if active_tenant is not None:
            # Single active tenant: only its LOCAL memberships, and only if the
            # requester may view them here. No mixed rows, so no Tenant column.
            if user.is_superuser or user.has_perm(
                'organization.view_membership', obj=active_tenant,
            ):
                return qs.filter(tenant=active_tenant)
            return qs.none()
        # Group scope or superuser global: memberships from the tenants in the
        # active context. Compute that set from request.active_tenant_group (stable)
        # rather than Tenant.objects — the permission backend's obj=None resolution
        # clobbers the current-tenant contextvar to the user's first membership
        # while checking the ambient view_membership perm, which would otherwise
        # mis-scope the group query to that single tenant.
        scoped_ids = self._context_tenant_ids(user, active_group)
        if user.is_superuser:
            return qs.filter(tenant_id__in=scoped_ids)
        allowed = [
            t.pk for t in Tenant._base_manager.filter(
                pk__in=scoped_ids, deleted_at__isnull=True,
            )
            if user.has_perm('organization.view_membership', obj=t)
        ]
        return qs.filter(tenant_id__in=allowed)

    def _context_tenant_ids(self, user, active_group):
        """Tenant ids visible in the active group / global context (see get_queryset).

        Group scope → the group subtree's tenants, intersected with the canonical
        accessible set for a non-superuser. Global (superuser, no active tenant or
        group) → every tenant. A non-superuser never reaches the global case
        (middleware always resolves them an active tenant).
        """
        # inline import: keep accessible_tenant_ids as the single source of truth
        # without a module-load cycle risk.
        from organization.access import accessible_tenant_ids, get_descendant_tenant_group_ids
        if active_group is not None:
            group_ids = get_descendant_tenant_group_ids(active_group.pk)
            base = Tenant._base_manager.filter(
                group_id__in=group_ids, deleted_at__isnull=True,
            )
            if not user.is_superuser:
                base = base.filter(pk__in=accessible_tenant_ids(user))
            return set(base.values_list('pk', flat=True))
        if user.is_superuser:
            return set(
                Tenant._base_manager.filter(deleted_at__isnull=True).values_list('pk', flat=True)
            )
        return set()

    def _show_tenant_column(self):
        """Show the Tenant column only when rows may span tenants (group scope or
        superuser global) — never under a single active tenant."""
        return getattr(self.request, 'active_tenant', None) is None

    def get_table(self):
        # Hard-exclude the Tenant column under a single active tenant (every row
        # shares it); keep it otherwise so a mixed table always identifies each row.
        exclude = () if self._show_tenant_column() else ('tenant',)
        return self.table(self.object_list, request=self.request, exclude=exclude)

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


class _MembershipFormViewMixin:
    """Shared context wiring for the create/edit membership screens.

    The membership template wraps its own ``<form>`` so it can embed the managed
    grants formset that ``MembershipForm`` owns; expose that formset to the
    template so it renders (and its errors survive form_invalid re-render).
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get('form')
        context['managed_formset'] = getattr(form, 'managed_formset', None)
        return context


class MembershipCreateView(_MembershipFormViewMixin, ObjectEditView):
    """The unified "Add member" flow: who / this-organization / managed tenants in one form.

    The tenant this membership will belong to is AUTHORIZED before the form is
    built or rendered: an explicit ``?tenant=<pk>`` the requester may not add
    members to 404s (never confirming its existence, never leaking its roles /
    managed tenants), and only a superuser gets the context-free global picker.
    """
    queryset = Membership.objects.all()
    model = Membership
    model_form = MembershipForm
    template_name = 'organization/memberships/membership_form.html'

    _AUTHZ_UNSET = object()

    def _authorized_tenant(self):
        """Resolve + authorize the tenant this membership will belong to (cached).

        An explicit ``?tenant=`` is authorized as a deep link; without it the
        ambient active tenant is used. In both cases the requester must be a
        superuser or hold ``organization.add_membership`` on that tenant. An
        explicitly requested tenant the requester may not use (missing, deleted,
        or unauthorized) raises ``Http404`` so the endpoint does not confirm it
        exists. Returns ``None`` only for a context-free request (no ?tenant, no
        usable active tenant) — reserved for superusers by ``has_permission()``.
        """
        cached = getattr(self, '_authorized_tenant_cache', self._AUTHZ_UNSET)
        if cached is not self._AUTHZ_UNSET:
            return cached

        user = self.request.user
        tenant_param = self.request.GET.get('tenant')
        explicit = bool(tenant_param)
        tenant = None
        if explicit:
            try:
                tenant = Tenant._base_manager.filter(
                    pk=tenant_param, deleted_at__isnull=True,
                ).first()
            except (TypeError, ValueError):  # non-numeric ?tenant= must not 500
                tenant = None
        else:
            tenant = getattr(self.request, 'active_tenant', None)

        authorized = None
        if tenant is not None and (
            user.is_superuser
            or user.has_perm('organization.add_membership', obj=tenant)
        ):
            authorized = tenant

        # A deep link to a tenant the requester may not use must be indistinguishable
        # from one that does not exist — 404, do not fall back to the active tenant.
        if explicit and authorized is None:
            raise Http404("No membership can be added to the requested tenant.")

        self._authorized_tenant_cache = authorized
        return authorized

    def has_permission(self):
        authorized = self._authorized_tenant()  # may 404 an explicit unauthorized tenant
        if authorized is not None:
            return True
        # No authorized tenant context: only a superuser may get the context-free
        # global picker. Everyone else fails closed (no ambient add-membership right).
        return bool(self.request.user.is_superuser)

    def get_initial(self):
        initial = super().get_initial()
        # ?user= prefills the member select; the tenant is governed by
        # _authorized_tenant() (the form's tenant= kwarg), never by a raw ?tenant.
        user_param = self.request.GET.get('user')
        if user_param:
            initial['user'] = user_param
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['tenant'] = self._authorized_tenant()
        # ?preset=technician (the redirected quick-onboard flow) preselects
        # who=new + one managed/all Technician grant row.
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


class MembershipEditView(_MembershipFormViewMixin, ObjectEditView):
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
