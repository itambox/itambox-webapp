from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.views.generic import View, UpdateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from django.views.generic.base import TemplateResponseMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from .models import UserPreference
from core.models import ObjectChange
from core.tables import ObjectChangeTable
from assetbox.utils import get_paginate_count
from assetbox.views.generic import BaseHTMXView
from django_tables2 import SingleTableView, RequestConfig
from .forms import UserProfileForm, UserPreferencesForm

User = get_user_model()

# User Account Views
class UserProfileView(LoginRequiredMixin, BaseHTMXView, UpdateView):
    model = User
    form_class = UserProfileForm
    template_name = 'users/profile.html'
    page_body_partial_name = "users/partials/user_page_body_wrapper.html" # Use User wrapper
    success_url = reverse_lazy('users:user_profile')

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profile updated successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'profile'
        context['user'] = self.request.user
        context['user_groups'] = self.request.user.groups.all()
        activity_qs = ObjectChange.objects.filter(user=self.request.user)[:15]
        activity_table = ObjectChangeTable(activity_qs, request=self.request)
        RequestConfig(self.request, paginate=False).configure(activity_table)
        context['activity_table'] = activity_table
        context['title'] = "User Profile"
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), 'Dashboard'),
            (None, context['title'])
        ]
        context['page_pretitle'] = "User Account" # Add pretitle for wrapper
        return context

    # render_to_response handled by BaseHTMXView

class UserPasswordView(LoginRequiredMixin, BaseHTMXView, DjangoPasswordChangeView):
    template_name = 'users/password.html'
    page_body_partial_name = "users/partials/user_page_body_wrapper.html" # Use User wrapper
    success_url = reverse_lazy('users:user_profile')

    def form_valid(self, form):
        messages.success(self.request, "Password changed successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'password'
        context['user'] = self.request.user
        context['title'] = "Change Password"
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), 'Dashboard'),
            (reverse_lazy('users:user_profile'), 'User Profile'),
            (None, context['title'])
        ]
        context['page_pretitle'] = "User Account" # Add pretitle for wrapper
        return context

    # render_to_response handled by BaseHTMXView

class UserPreferencesView(LoginRequiredMixin, BaseHTMXView, TemplateResponseMixin, View):
    form_class = UserPreferencesForm
    template_name = 'users/preferences.html'
    page_body_partial_name = "users/partials/user_page_body_wrapper.html" # Use User wrapper

    def get_context_data(self, request, **kwargs):
        context = kwargs
        context['active_tab'] = 'preferences'
        context['user'] = request.user
        context['title'] = "Preferences"
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), 'Dashboard'),
            (reverse_lazy('users:user_profile'), 'User Profile'),
            (None, context['title'])
        ]
        context['page_pretitle'] = "User Account"
        # Ensure form is in context if not already passed (e.g., for initial GET)
        if 'form' not in context:
             context['form'] = self.form_class(user=request.user)
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(request)
        # BaseHTMXView will call TemplateResponseMixin.render_to_response via super()
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Preferences saved successfully.")
            return redirect('users:user_preferences')
        else:
            messages.error(request, "There was an error saving your preferences.")
        # Pass invalid form back to context
        context = self.get_context_data(request, form=form)
        # BaseHTMXView will call TemplateResponseMixin.render_to_response via super()
        return self.render_to_response(context)

    # REMOVED custom render_to_response method


# Dummy Views for other tabs
class UserGenericTabView(LoginRequiredMixin, BaseHTMXView, TemplateView):
    template_name = 'users/dummy_tab.html'
    page_body_partial_name = "users/partials/user_page_body_wrapper.html" # Use User wrapper
    active_tab = ''
    tab_title = 'User Tab' # Add a default title

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.active_tab
        context['user'] = self.request.user # Ensure user is in context

        # --- Add Breadcrumbs & Title for BaseHTMXView --- 
        context['title'] = self.tab_title # Use the specific tab title
        context['breadcrumbs'] = [
            (reverse_lazy('dashboard'), 'Dashboard'),
            (reverse_lazy('users:user_profile'), 'User Profile'), # Link back to profile
            (None, context['title'])
        ]
        # --- End Breadcrumbs & Title ---
        context['page_pretitle'] = "User Account" # Add pretitle for wrapper
        return context

    # render_to_response handled by BaseHTMXView

class UserApiTokensView(UserGenericTabView):
    active_tab = 'api_tokens'
    tab_title = 'API Tokens'
    template_name = 'users/api_tokens.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import Token
        from .forms import TokenForm
        context['tokens'] = Token.objects.filter(user=self.request.user).order_by('-created')
        if 'form' not in context:
            context['form'] = TokenForm()
        if 'new_token_key' in self.request.session:
            context['new_token_key'] = self.request.session.pop('new_token_key')
        return context

    def post(self, request, *args, **kwargs):
        from .forms import TokenForm
        form = TokenForm(request.POST)
        if form.is_valid():
            token = form.save(commit=False)
            token.user = request.user
            if form.cleaned_data.get('expires'):
                from django.utils import timezone
                import datetime
                expires_date = form.cleaned_data['expires']
                token.expires = timezone.make_aware(
                    datetime.datetime.combine(expires_date, datetime.time.max)
                )
            token.save()
            form.save_m2m()
            messages.success(
                request,
                f"API Token generated successfully! Make sure to copy your new personal access token now, as you won't be able to see it again: <code>{token.key}</code>"
            )
            request.session['new_token_key'] = token.key
            return redirect('users:user_api_tokens')
        
        context = self.get_context_data(form=form)
        return self.render_to_response(context)


class UserNotificationsView(UserGenericTabView):
    active_tab = 'notifications'
    tab_title = 'Notifications'
    template_name = 'users/notifications.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from core.models import Notification
        context['notifications'] = Notification.objects.filter(user=self.request.user).order_by('-created_at')
        return context


@login_required
@require_POST
def mark_notification_read(request, pk):
    from core.models import Notification
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.is_read = True
    notification.save()
    return redirect('users:user_notifications')


@login_required
def view_notification(request, pk):
    from core.models import Notification
    from django.db.models import Q
    notification = get_object_or_404(Notification, Q(user=request.user) | Q(user__isnull=True), pk=pk)
    
    if notification.user == request.user:
        notification.is_read = True
        notification.save()
        
    if notification.target_url:
        return redirect(notification.target_url)
    return redirect('users:user_notifications')


@login_required
@require_POST
def mark_all_notifications_read(request):
    from core.models import Notification
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect('users:user_notifications')


@login_required
@require_POST
def delete_api_token(request, pk):
    from .models import Token
    token = get_object_or_404(Token, pk=pk, user=request.user)
    token.delete()
    messages.success(request, "API Token has been revoked.")
    return redirect('users:user_api_tokens')



@login_required
def notification_poll(request):
    """HTMX polling endpoint returning notification dropdown content.
    
    Returns the notification bell dropdown HTML and an OOB badge update.
    Polled every 30 seconds by the topbar notification bell.
    Returns 204 No Content for non-HTMX requests.
    """
    if not getattr(request, 'htmx', False):
        return HttpResponse(status=204)

    from core.models import Notification
    unread_qs = Notification.objects.filter(user=request.user, is_read=False)
    context = {
        'unread_notifications_count': unread_qs.count(),
        'recent_unread_notifications': unread_qs.order_by('-created_at')[:5],
    }
    return render(request, 'htmx/notification_dropdown.html', context)


class UserSubscriptionsView(UserGenericTabView):
    active_tab = 'subscriptions'
    tab_title = 'Subscriptions'
    template_name = 'users/subscriptions.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from subscriptions.models import Subscription
        from subscriptions.tables import SubscriptionTable
        from django_tables2 import RequestConfig
        
        owned_subs = Subscription.objects.filter(owner=self.request.user).select_related('provider', 'tenant')
        
        # Configure SubscriptionTable for a clean read-only view
        subs_table = SubscriptionTable(owned_subs, request=self.request)
        subs_table.exclude = ('pk', 'actions')
        RequestConfig(self.request, paginate={'per_page': 10}).configure(subs_table)
        
        context['subscriptions_table'] = subs_table
        context['subscriptions_count'] = owned_subs.count()
        context['active_subscriptions_count'] = owned_subs.filter(status='active').count()
        
        # Annual Spend calculation
        total_annual_spend = sum(
            sub.annual_cost for sub in owned_subs 
            if sub.status == 'active' and sub.annual_cost is not None
        )
        context['total_annual_spend'] = total_annual_spend
        
        # Expiring in next 30 days
        expiring_soon_count = sum(
            1 for sub in owned_subs 
            if sub.status == 'active' and sub.days_until_renewal is not None and 0 <= sub.days_until_renewal <= 30
        )
        context['expiring_soon_count'] = expiring_soon_count
        
        # Overdue renewals
        overdue_count = sum(
            1 for sub in owned_subs 
            if sub.status == 'expired' or (sub.status == 'active' and sub.is_expired)
        )
        context['overdue_count'] = overdue_count
        
        return context

