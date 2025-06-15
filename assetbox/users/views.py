from django.shortcuts import render, redirect
from django.views.generic import View, UpdateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from .models import UserPreference
from core.models import ObjectChange
from core.tables import ObjectChangeTable
from core.utils import get_paginate_count
from django_tables2 import SingleTableView, RequestConfig
from .forms import UserProfileForm, UserPreferencesForm

User = get_user_model()

# User Account Views
class UserProfileView(LoginRequiredMixin, UpdateView):
    model = User
    form_class = UserProfileForm
    template_name = 'users/profile.html' # Path relative to TEMPLATES DIRS
    success_url = reverse_lazy('users:user_profile') # Update URL name

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profile updated successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'profile'

        # Add user groups
        context['user_groups'] = self.request.user.groups.all()

        # Add recent activity log
        activity_qs = ObjectChange.objects.filter(user=self.request.user)[:15] # Limit to last 15 changes
        activity_table = ObjectChangeTable(activity_qs, request=self.request)
        # Apply basic config (no pagination needed for this short list)
        RequestConfig(self.request, paginate=False).configure(activity_table) 
        context['activity_table'] = activity_table

        return context

class UserPasswordView(LoginRequiredMixin, DjangoPasswordChangeView):
    template_name = 'users/password.html' # Path relative to TEMPLATES DIRS
    success_url = reverse_lazy('users:user_profile') # Update URL name

    def form_valid(self, form):
        messages.success(self.request, "Password changed successfully.")
        return super().form_valid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = 'password'
        return context

class UserPreferencesView(LoginRequiredMixin, View):
    form_class = UserPreferencesForm
    template_name = 'users/preferences.html'

    def get(self, request, *args, **kwargs):
        # Pass the user to the form constructor
        form = self.form_class(user=request.user)
        return render(request, self.template_name, {'form': form})

    def post(self, request, *args, **kwargs):
        # Pass the user *and* request.POST data to the form constructor
        form = self.form_class(request.user, request.POST)
        if form.is_valid():
            form.save() # Call the form's save method
            messages.success(request, "Preferences saved successfully.")
            # Use namespaced URL name for redirect
            return redirect('users:user_preferences') 
        else:
            messages.error(request, "There was an error saving your preferences.")
        return render(request, self.template_name, {'form': form})

# Dummy Views for other tabs
class UserGenericTabView(LoginRequiredMixin, TemplateView):
    template_name = 'users/dummy_tab.html' # Path relative to TEMPLATES DIRS
    active_tab = '' 

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.active_tab
        context['user'] = self.request.user
        return context

class UserApiTokensView(UserGenericTabView):
    active_tab = 'api_tokens'

class UserNotificationsView(UserGenericTabView):
    active_tab = 'notifications'

class UserSubscriptionsView(UserGenericTabView):
    active_tab = 'subscriptions'
