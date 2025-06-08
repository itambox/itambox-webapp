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
    template_name = 'users/profile.html' # Update template path
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
    template_name = 'users/password.html' # Update template path
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

    def _get_preference(self, user):
        preference, _ = UserPreference.objects.get_or_create(user=user)
        return preference

    def _get_table_configs(self, preference):
        configs = []
        table_configs = preference.data.get('tables', {})
        for table_key, config in table_configs.items():
            # Simple display for now, assumes key is like 'assets.AssetTable'
            table_name = table_key.split('.')[-1] if '.' in table_key else table_key
            configs.append({
                'key': table_key,
                'name': table_name,
                'columns': ", ".join(config.get('columns', [])),
                'ordering': ", ".join(config.get('ordering', [])),
            })
        return sorted(configs, key=lambda x: x['name']) # Sort alphabetically

    def get(self, request):
        preference = self._get_preference(request.user)
        initial_data = {
            'pagination_per_page': preference.data.get('pagination', {}).get('per_page', 25),
            'theme': preference.data.get('ui', {}).get('theme', UserPreference.THEME_LIGHT),
        }
        form = self.form_class(initial=initial_data)
        table_configs = self._get_table_configs(preference)
        
        context = {
            'form': form,
            'table_configs': table_configs,
            'active_tab': 'preferences',
            'user': request.user,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        preference = self._get_preference(request.user)
        form = self.form_class(request.POST)
        # Checkboxes for clearing table configs will have name 'pk' and value as table_key
        clear_tables = request.POST.getlist('pk') 
        
        if form.is_valid():
            # Save general preferences
            if 'pagination' not in preference.data: preference.data['pagination'] = {}
            if 'ui' not in preference.data: preference.data['ui'] = {}
            preference.data['pagination']['per_page'] = form.cleaned_data['pagination_per_page']
            preference.data['ui']['theme'] = form.cleaned_data['theme']
            
            # Clear selected table configs
            if clear_tables:
                if 'tables' in preference.data:
                    for table_key in clear_tables:
                        if table_key in preference.data['tables']:
                            del preference.data['tables'][table_key]
                            messages.info(request, f"Cleared saved configuration for {table_key}")
                    # Clean up empty 'tables' dict if needed
                    if not preference.data['tables']:
                        del preference.data['tables']
                
            preference.save()
            messages.success(request, "Preferences updated successfully.")
            return redirect('users:user_preferences') 
        
        # If form is invalid, re-render with errors and existing table configs
        table_configs = self._get_table_configs(preference)
        context = {
            'form': form,
            'table_configs': table_configs,
            'active_tab': 'preferences',
            'user': request.user,
        }
        return render(request, self.template_name, context)

# Dummy Views for other tabs
class UserGenericTabView(LoginRequiredMixin, TemplateView):
    template_name = 'users/dummy_tab.html' # Update template path
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
