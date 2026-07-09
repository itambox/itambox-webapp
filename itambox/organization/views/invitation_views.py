import uuid
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.generic import CreateView, View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.utils.translation import gettext_lazy as _
from ..models import TenantInvitation, accept_invitation
from ..forms import TenantInvitationForm

class InviteUserMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        if self.request.user.is_superuser:
            return True
        # Gate strictly on the real permission resolved through the MembershipBackend
        # against the active tenant. The former ``role.name == 'admin'`` string branch was
        # a backdoor: any role literally named "admin" (regardless of permissions) granted
        # invite access. Authorization must flow through has_perm only.
        active_tenant = getattr(self.request, 'active_tenant', None)
        if active_tenant is None:
            return False
        return self.request.user.has_perm(
            'organization.add_tenantinvitation', obj=active_tenant
        )

class InviteUserView(InviteUserMixin, CreateView):
    model = TenantInvitation
    form_class = TenantInvitationForm
    template_name = 'organization/invite_user.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = getattr(self.request, 'active_tenant', None)
        kwargs['requesting_user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        active_tenant = getattr(self.request, 'active_tenant', None)
        if not active_tenant:
            messages.error(self.request, _("You must select an active workspace before inviting members."))
            return redirect('dashboard')

        # Save invitation details
        invitation = form.save(commit=False)
        invitation.tenant = active_tenant
        invitation.invited_by = self.request.user
        invitation.expires_at = timezone.now() + timezone.timedelta(days=7) # 7 days expiry
        invitation.save()

        # Generate local accept link for easy testing and display in alert
        accept_url = self.request.build_absolute_uri(
            reverse('organization:accept_invitation', kwargs={'token': invitation.token})
        )

        messages.success(
            self.request,
            _("Successfully generated an invitation for %(email)s! Share this link: %(url)s") % {
                'email': invitation.email,
                'url': accept_url,
            }
        )
        return redirect(active_tenant.get_absolute_url())


class AcceptInvitationView(View):
    def get(self, request, token):
        try:
            invitation = TenantInvitation.objects.get(token=token)
        except (TenantInvitation.DoesNotExist, ValueError):
            messages.error(request, _("This invitation link is invalid or has expired."))
            return redirect('login')

        if not invitation.is_valid:
            messages.error(request, _("This invitation has already been accepted or has expired."))
            return redirect('login')

        if not request.user.is_authenticated:
            # Redirect to login, passing the current path as 'next'
            messages.info(request, _("Please sign in or register to accept your invitation to %(tenant)s.") % {'tenant': invitation.tenant.name})
            return redirect(f"{reverse('login')}?next={request.path}")

        # Bind the invitation to its intended recipient: the signed-in account's
        # email must match the address the invite was issued to. Without this, any
        # authenticated user holding the link could join the tenant at the invited
        # role (potentially Admin).
        user_email = (request.user.email or '').strip().lower()
        invite_email = (invitation.email or '').strip().lower()
        if user_email != invite_email:
            messages.error(
                request,
                _("This invitation was issued to %(email)s. "
                  "Please sign in with that account to accept it.") % {'email': invitation.email}
            )
            return redirect('dashboard')

        # Accept the invitation
        accept_invitation(invitation, request.user)

        messages.success(request, _("Welcome! You have successfully joined the workspace %(tenant)s.") % {'tenant': invitation.tenant.name})
        
        # Switch the active workspace to the newly joined tenant
        request.session['active_tenant_id'] = invitation.tenant.id
        
        return redirect('dashboard')
