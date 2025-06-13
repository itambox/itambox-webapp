# assetbox/core/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.apps import apps # To find models/tables dynamically
from django.urls import reverse
from importlib import import_module
from django.http import Http404
from django.views.generic import DetailView
from django_tables2 import SingleTableView
from django.utils.decorators import method_decorator
import json
from django.core.serializers.json import DjangoJSONEncoder
import difflib
from django.conf import settings # Add settings
from django.contrib.auth import get_user_model # Import get_user_model
from users.forms import TableConfigForm # Correct import
from users.models import UserPreference # Import UserPreference from users
from django.template.loader import get_template # Import get_template

# --- New Imports for SearchView ---
from django.views.generic import View  # Add View
from django.utils.module_loading import import_string # Add import_string

# Only import ObjectChange from core models now
from .models import ObjectChange 
from .tables import ObjectChangeTable
# from .tables.base import SESSION_KEY_PREFIX # No longer needed

# --- New Form/Table Imports for SearchView ---
from .forms import SearchForm # Add SearchForm
from .tables import SearchResultTable # Add SearchResultTable

# --- Model Imports for Debugging --- 
# Use the direct app name import path
from assets.models import AssetRole, Manufacturer 

from django.views.generic import View, ListView, DetailView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from .models import ObjectChange
from .tables import ObjectChangeTable
# from .filters import ObjectChangeFilterSet # Comment out unused import
from .utils import get_model_viewname, get_paginate_count, get_table_for_model # Import the new helper
from django.contrib.contenttypes.models import ContentType # Add ContentType
from django.utils.http import urlencode
from django.views.decorators.http import require_POST # Add require_POST

User = get_user_model() # Get the User model

@login_required
def table_config(request, model_name):
    """
    Handle modal display and saving of table configuration.
    """
    # Get the relevant Table class
    app_label, table_part = model_name.split('.')
    app_config = apps.get_app_config(app_label)
    table_module = import_string(f'{app_config.name}.tables')
    TableClass = getattr(table_module, table_part)
    
    table = TableClass([]) # Instantiate with empty data just to get columns
    table_verbose_name = TableClass.Meta.model._meta.verbose_name_plural.title()
    
    # --- Load User Preferences --- 
    prefs, _ = UserPreference.objects.get_or_create(user=request.user)
    # Use the app_label and table_part separately to access nested dict
    table_key_for_form = f'{app_label}.{table_part}' # Key for the form/JS
    user_config = prefs.data.get('tables', {}).get(app_label, {}).get(table_part, {}) # Get nested config
    print(f"[table_config] Fetched user_config for {app_label}.{table_part}: {user_config}") # DEBUG
    # --- End Load --- 

    # Pass user_config to the form constructor
    form = TableConfigForm(table=table, user_config=user_config) 

    # Use consolidated template path
    template = get_template('core/includes/table_config_modal.html')
    context = {
        'form': form,
        'table_name': table_key_for_form, # Use the consistent key for JS
        'table_verbose_name': table_verbose_name,
    }
    return HttpResponse(template.render(context, request))

# @login_required
# def user_preferences_view(request):
#     # ... (view logic) ...

# @login_required
# def table_config(request, model_name):
#     # ... (previous table_config implementation) ...
#     # ... (rest of the function remains unchanged) ...
#     # ... (return the same context) ...
#     # ... (return the same render call) ... 

@method_decorator(login_required, name='dispatch')
class ObjectChangeListView(SingleTableView):
    model = ObjectChange
    table_class = ObjectChangeTable
    template_name = 'core/objectchange/objectchange_list.html'
    context_object_name = 'object_changes'
    # Add pagination if desired
    # paginate_by = 25

    # Add filtering later if needed (using django-filter)
    # filterset_class = ObjectChangeFilterSet

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related(
            'user', 'changed_object_type', 'related_object_type'
        )
        # Add filtering logic here if using a filterset
        return queryset

@method_decorator(login_required, name='dispatch')
class ObjectChangeView(DetailView):
    model = ObjectChange
    template_name = 'core/objectchange/objectchange.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()

        prechange_data = obj.prechange_data or {}
        postchange_data = obj.postchange_data or {}

        # --- Server-side Diff Calculation (NetBox Style) ---
        # Get string representations for comparison
        prechange_string = json.dumps(prechange_data, cls=DjangoJSONEncoder, indent=2, sort_keys=True)
        postchange_string = json.dumps(postchange_data, cls=DjangoJSONEncoder, indent=2, sort_keys=True)

        # Split into lines for difflib
        prechange_lines = prechange_string.splitlines(keepends=True)
        postchange_lines = postchange_string.splitlines(keepends=True)

        # Generate diff using difflib.Differ
        differ = difflib.Differ()
        diff_lines = list(differ.compare(prechange_lines, postchange_lines))
        context['diff_lines'] = diff_lines
        # --- End Diff Calculation ---

        # Keep full JSON for reference if needed, but remove parts used only by JS
        context['prechange_data_json'] = prechange_string # Keep for potential reference
        context['postchange_data_json'] = postchange_string # Keep for potential reference

        # --- Calculate JSON subsets for the top "Difference" block (User Fixed - Keep As Is) ---
        diff_added_keys = {k for k, v in postchange_data.items() if k not in prechange_data or prechange_data[k] != v}
        diff_removed_keys = {k for k, v in prechange_data.items() if k not in postchange_data or postchange_data[k] != v}
        diff_added = {k: v for k, v in postchange_data.items() if k in diff_added_keys}
        diff_removed = {k: v for k, v in prechange_data.items() if k in diff_removed_keys}
        context['diff_added_json'] = json.dumps(diff_added, cls=DjangoJSONEncoder, indent=2)
        context['diff_removed_json'] = json.dumps(diff_removed, cls=DjangoJSONEncoder, indent=2)
        # --- End Difference Block Calculation ---

        # REMOVED context variables for JS highlighting
        # context['diff_added_keys'] = list(diff_added_keys)
        # context['diff_removed_keys'] = list(diff_removed_keys)

        return context 

# --- Search View ---
class SearchView(LoginRequiredMixin, View):
    template_name = 'core/search.html' 

    def get(self, request):
        query = request.GET.get('q', '').strip()
        # obj_type is now a list from the form
        obj_types = request.GET.getlist('obj_type') 
        lookup = request.GET.get('lookup', 'icontains')

        allowed_lookups = {'icontains', 'iexact', 'istartswith', 'iendswith', 'iregex'}
        if lookup not in allowed_lookups:
            lookup = 'icontains'

        form = SearchForm(request.GET)
        results_data = {}
        results_count = 0

        if query:
            search_backend_cls = import_string(settings.SEARCH_BACKEND)
            search_backend = search_backend_cls()
            # Pass the list of obj_types to the search backend
            results_data = search_backend.search(query, user=request.user, obj_types=obj_types, lookup=lookup) 

            # Process results for template (create tables, get counts)
            for model, data in results_data.items():
                results_count += data['count']
                # Instantiate table with limited results for preview
                table_class = get_table_for_model(model)
                if table_class:
                    # Limit results shown on search page (e.g., first 10)
                    data['table'] = table_class(data['queryset'][:10], request=request) 
                    # Add list URL (replace with actual logic if needed)
                    data['list_url'] = f'/{model._meta.app_label}/{model._meta.model_name}s/' 
                else:
                    data['table'] = None

        context = {
            'form': form,
            'query': query,
            'obj_types': obj_types, # Pass list to context
            'lookup': lookup,
            'results': results_data,
            'results_count': results_count,
        }
        return render(request, self.template_name, context)

# User Account Views
# class UserProfileView(LoginRequiredMixin, UpdateView):
#     model = User
#     form_class = UserProfileForm
#     template_name = 'core/user/profile.html'
#     success_url = reverse_lazy('user_profile')

#     def get_object(self, queryset=None):
#         return self.request.user # Get the currently logged-in user

#     def form_valid(self, form):
#         messages.success(self.request, "Profile updated successfully.")
#         return super().form_valid(form)

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         context['active_tab'] = 'profile'
#         return context

# class UserPasswordView(LoginRequiredMixin, DjangoPasswordChangeView):
#     template_name = 'core/user/password.html'
#     success_url = reverse_lazy('user_profile') # Redirect back to profile after success

#     def form_valid(self, form):
#         messages.success(self.request, "Password changed successfully.")
#         return super().form_valid(form)
    
#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         context['active_tab'] = 'password'
#         return context

# class UserPreferencesView(LoginRequiredMixin, View):
#     form_class = UserPreferencesForm
#     template_name = 'core/user/preferences.html'

#     def _get_preference(self, user):
#         preference, _ = UserPreference.objects.get_or_create(user=user)
#         return preference

#     def get(self, request):
#         preference = self._get_preference(request.user)
#         # Populate initial form data from the JSON field
#         initial_data = {
#             'pagination_per_page': preference.data.get('pagination', {}).get('per_page', 25), # Default 25
#             'theme': preference.data.get('ui', {}).get('theme', UserPreference.THEME_LIGHT), # Default light
#             # Add other initial data keys
#         }
#         form = self.form_class(initial=initial_data)
#         context = {
#             'form': form,
#             'active_tab': 'preferences',
#             'user': request.user,
#         }
#         return render(request, self.template_name, context)

#     def post(self, request):
#         preference = self._get_preference(request.user)
#         form = self.form_class(request.POST)
        
#         if form.is_valid():
#             # Update the data JSON field
#             # Ensure nested dictionaries exist
#             if 'pagination' not in preference.data:
#                 preference.data['pagination'] = {}
#             if 'ui' not in preference.data:
#                 preference.data['ui'] = {}
                
#             preference.data['pagination']['per_page'] = form.cleaned_data['pagination_per_page']
#             preference.data['ui']['theme'] = form.cleaned_data['theme']
#             # Add other keys to save
            
#             preference.save()
#             messages.success(request, "Preferences updated successfully.")
#             return redirect('user_preferences') # Redirect back to the same view
        
#         # If form is invalid, re-render with errors
#         context = {
#             'form': form,
#             'active_tab': 'preferences',
#             'user': request.user,
#         }
#         return render(request, self.template_name, context)

# Dummy Views for other tabs
# class UserGenericTabView(LoginRequiredMixin, TemplateView):
#     template_name = 'core/user/dummy_tab.html'
#     active_tab = '' # Subclasses should override

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         context['active_tab'] = self.active_tab
#         context['user'] = self.request.user # Pass user for the base template
#         return context

# class UserApiTokensView(UserGenericTabView):
#     active_tab = 'api_tokens'

# class UserNotificationsView(UserGenericTabView):
#     active_tab = 'notifications'

# class UserSubscriptionsView(UserGenericTabView):
#     active_tab = 'subscriptions' 

# Generic Bulk Delete View
@login_required
@require_POST # Only allow POST requests
def bulk_delete(request):
    model_name = request.POST.get('model_name') # e.g., "assets.Asset"
    object_pks = request.POST.getlist('pk') # List of primary keys to delete
    return_url = request.META.get('HTTP_REFERER', reverse('dashboard')) # Where to redirect back

    if not model_name or not object_pks:
        messages.error(request, "Missing model name or object IDs for bulk deletion.")
        return redirect(return_url)

    try:
        app_label, model_lower = model_name.split('.')
        model = apps.get_model(app_label=app_label, model_name=model_lower)
    except (ValueError, LookupError):
        messages.error(request, f"Invalid model specified: {model_name}")
        return redirect(return_url)

    # Check delete permission for the model
    # Simplified check - assumes delete perm follows standard pattern
    delete_perm = f'{app_label}.delete_{model_lower}'
    if not request.user.has_perm(delete_perm):
        messages.error(request, f"You do not have permission to delete {model._meta.verbose_name_plural}.")
        return redirect(return_url)
        
    queryset = model.objects.filter(pk__in=object_pks)
    objects_to_delete = list(queryset) # Evaluate queryset
    
    if not objects_to_delete:
        messages.warning(request, f"No valid {model._meta.verbose_name_plural} selected for deletion.")
        return redirect(return_url)

    # Handle the two POST scenarios
    if '_confirm' in request.POST:
        # --- User has confirmed deletion --- 
        try:
            count = len(objects_to_delete)
            model.objects.filter(pk__in=object_pks).delete() # Perform bulk delete
            messages.success(request, f"Successfully deleted {count} {model._meta.verbose_name_plural}.")
            return redirect(return_url)
        except ProtectedError as e:
            # Handle protected objects (optional, basic message for now)
            messages.error(request, f"Could not delete objects due to protected relationships: {e}")
            return redirect(return_url)
            
    else:
        # --- Initial POST from list view - Show confirmation --- 
        context = {
            'model_name': model_name,
            'model_verbose_name': model._meta.verbose_name,
            'model_verbose_name_plural': model._meta.verbose_name_plural,
            'objects': objects_to_delete,
            'object_pks': object_pks, # Pass PKs back to the confirmation form
            'return_url': return_url, 
        }
        return render(request, 'generic/object_confirm_bulk_delete.html', context) 