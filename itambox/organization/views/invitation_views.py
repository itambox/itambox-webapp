import uuid
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.generic import CreateView, View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from ..models import TenantInvitation, TenantRole, accept_invitation
from ..forms import TenantInvitationForm

class InviteUserMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        if self.request.user.is_superuser:
            return True
        membership = getattr(self.request, 'active_membership', None)
        if membership and membership.role:
            return 'organization.add_tenantinvitation' in membership.role.permissions or membership.role.name.lower() == 'admin'
        return False

class InviteUserView(InviteUserMixin, CreateView):
    model = TenantInvitation
    form_class = TenantInvitationForm
    template_name = 'organization/invite_user.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = getattr(self.request, 'active_tenant', None)
        return kwargs

    def form_valid(self, form):
        active_tenant = getattr(self.request, 'active_tenant', None)
        if not active_tenant:
            messages.error(self.request, "You must select an active workspace before inviting members.")
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
            f"Successfully generated an invitation for {invitation.email}! Share this link: {accept_url}"
        )
        return redirect(active_tenant.get_absolute_url())


class AcceptInvitationView(View):
    def get(self, request, token):
        try:
            invitation = TenantInvitation.objects.get(token=token)
        except (TenantInvitation.DoesNotExist, ValueError):
            messages.error(request, "This invitation link is invalid or has expired.")
            return redirect('login')

        if not invitation.is_valid:
            messages.error(request, "This invitation has already been accepted or has expired.")
            return redirect('login')

        if not request.user.is_authenticated:
            # Redirect to login, passing the current path as 'next'
            messages.info(request, f"Please sign in or register to accept your invitation to {invitation.tenant.name}.")
            return redirect(f"{reverse('login')}?next={request.path}")

        # Accept the invitation
        accept_invitation(invitation, request.user)

        messages.success(request, f"Welcome! You have successfully joined the workspace {invitation.tenant.name}.")
        
        # Switch the active workspace to the newly joined tenant
        request.session['active_tenant_id'] = invitation.tenant.id
        
        return redirect('dashboard')
