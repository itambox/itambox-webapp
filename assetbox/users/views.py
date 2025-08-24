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
    tab_title = 'API Tokens' # Specific title

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
@require_POST
def mark_all_notifications_read(request):
    from core.models import Notification
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect('users:user_notifications')


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
    tab_title = 'Subscriptions' # Specific title
