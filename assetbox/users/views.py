from django.shortcuts import render, redirect
from django.views.generic import View, UpdateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from core.models import UserPreference # Import UserPreference from core
from .forms import UserProfileForm, UserPreferencesForm # Import forms from this app

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
    template_name = 'users/preferences.html' # Update template path

    def _get_preference(self, user):
        preference, _ = UserPreference.objects.get_or_create(user=user)
        return preference

    def get(self, request):
        preference = self._get_preference(request.user)
        initial_data = {
            'pagination_per_page': preference.data.get('pagination', {}).get('per_page', 25),
            'theme': preference.data.get('ui', {}).get('theme', UserPreference.THEME_LIGHT),
        }
        form = self.form_class(initial=initial_data)
        context = {
            'form': form,
            'active_tab': 'preferences',
            'user': request.user,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        preference = self._get_preference(request.user)
        form = self.form_class(request.POST)
        
        if form.is_valid():
            if 'pagination' not in preference.data:
                preference.data['pagination'] = {}
            if 'ui' not in preference.data:
                preference.data['ui'] = {}
                
            preference.data['pagination']['per_page'] = form.cleaned_data['pagination_per_page']
            preference.data['ui']['theme'] = form.cleaned_data['theme']
            
            preference.save()
            messages.success(request, "Preferences updated successfully.")
            return redirect('users:user_preferences') # Update URL name
        
        context = {
            'form': form,
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
