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

from django.views.generic import View, ListView, DetailView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.urls import reverse_lazy, reverse, NoReverseMatch
from django.contrib import messages
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from .models import ObjectChange
from .tables import ObjectChangeTable
# from .filters import ObjectChangeFilterSet # Comment out unused import
from .utils import get_model_viewname, get_paginate_count, get_table_for_model # Import the new helper
from django.contrib.contenttypes.models import ContentType # Add ContentType
from django.utils.http import urlencode
from django.views.decorators.http import require_POST # Add require_POST
from django.db.models import ProtectedError # To handle deletion prevention
from .forms import ConfirmationForm # A generic confirmation form

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

# =============================================================================
# Generic Object Views (NetBox-inspired base classes)
# =============================================================================

class ObjectListView(LoginRequiredMixin, ListView):
    """Base view for listing objects."""
    filterset = None
    filterset_form = None
    table = None
    template_name = 'generic/object_list.html' # Default template
    # template_name_partial = 'generic/partials/object_list_content_wrapper.html' # Define partial template name
    # action_buttons = () # Tuple of actions ('add', 'import', 'export')

    # --- Add get method to handle HTMX ---
    # REMOVED custom get method - render_to_response is cleaner for CBVs

    # --- Add render_to_response to handle HTMX requests ---
    def render_to_response(self, context, **response_kwargs):
        """
        Override render_to_response to handle HTMX requests.
        - Renders the full view template for other HTMX requests (e.g., hx-boost).
        """
        # --- DEBUGGING HTMX CHECK ---
        # print(f"[DEBUG] Inside render_to_response: request.htmx = {getattr(self.request, 'htmx', 'AttributeNotFound')}") # Keep commented out for now
        # --- END DEBUGGING ---
        if self.request.htmx:
            # Check if the target is the specific object list content div
            if self.request.headers.get('HX-Target') == 'object-list-content':
                # Render only the content wrapper partial for table updates
                print("[DEBUG] HTMX request targeting #object-list-content: Rendering partial object_list_content_wrapper.html") # DEBUG
                context['request'] = self.request
                return render(
                    self.request,
                    'generic/partials/object_list_content_wrapper.html', # Use the correct partial for table swaps
                    context
                )
            else:
                # For other HTMX requests (like hx-boost), render the new HTMX page wrapper partial.
                # This partial contains the main content block and OOB blocks.
                print(f"[DEBUG] HTMX request targeting other ({self.request.headers.get('HX-Target', 'N/A')}): Rendering new partial htmx_list_page_wrapper.html") # DEBUG
                context['request'] = self.request
                return render(
                    self.request,
                    'generic/partials/htmx_list_page_wrapper.html', # Use the NEW partial for boosted requests
                    context
                )
                # pass # No longer falling through
        # else: # No need for explicit else
        #     print("[DEBUG] Standard request: Rendering full page") # DEBUG

        # Standard rendering (for non-HTMX requests)
        return super().render_to_response(context, **response_kwargs)
    # --- End render_to_response ---

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.filterset:
            self.filter = self.filterset(self.request.GET, queryset)
            if not self.filter.is_valid():
                # Handle invalid filter data if necessary (e.g., log or message)
                # For now, just return the unfiltered queryset or apply default filters
                pass 
            return self.filter.qs
        return queryset

    def get_paginate_by(self, queryset):
        # Ensure ListView's built-in pagination is disabled
        # Rely solely on RequestConfig in get_context_data
        return None

    def get_table(self):
        """Return the django-tables2 Table instance."""
        queryset = self.get_queryset()
        # Allow specifying table class via attribute or method
        table_class = self.table or get_table_for_model(self.model)
        if not table_class:
            raise Http404(f"No table defined for model {self.model._meta.model_name}")
        
        table = table_class(queryset, request=self.request)
        # REMOVED pagination logic from here - Handled by RequestConfig in get_context_data
        # paginate = {
        #     'paginator_class': self.paginator_class,
        #     'per_page': self.get_paginate_by(queryset)
        # }
        # table.paginate(**paginate)
        return table

    def get_context_data(self, **kwargs):
        # Ensure self.model is set before calling super() or accessing _meta
        # It should normally be set by ListView if queryset is defined,
        # but we add a safeguard.
        _model = getattr(self, 'model', None)
        if not _model and self.queryset is not None:
            _model = self.queryset.model
        elif not _model and hasattr(self, 'object_list') and self.object_list is not None:
             # Fallback if queryset isn't used directly but object_list is populated
            _model = self.object_list.model
        
        if not _model:
             # If we still can't determine the model, something is wrong
             raise ImproperlyConfigured(
                 f"{self.__class__.__name__} is missing a QuerySet. Define "
                 f"{self.__class__.__name__}.model, {self.__class__.__name__}.queryset, or override "
                 f"{self.__class__.__name__}.get_queryset()."
             )

        # Call super() AFTER we've potentially identified the model
        context = super().get_context_data(**kwargs)
        
        # --- Set self.model before calling get_table --- 
        self.model = _model
        # --- End Set self.model ---

        table = self.get_table()
        filter_form = self.filterset_form(self.request.GET) if self.filterset_form else None
        context['table'] = table
        context['filter_form'] = filter_form
        context['model'] = _model # Use the determined model
        context['verbose_name_plural'] = _model._meta.verbose_name_plural
        # --- Add model_name_str --- 
        context['model_name_str'] = f"{_model._meta.app_label}.{_model._meta.model_name}"
        # --- End Add model_name_str --- 
        # --- Add table_config_key --- 
        context['table_config_key'] = f"{_model._meta.app_label}.{table.__class__.__name__}" # Key for config modal URL
        # --- End Add table_config_key --- 

        # Ensure add_url also uses the determined model
        try:
             context['add_url'] = reverse(f'{_model._meta.app_label}:{_model._meta.model_name}_add')
        except NoReverseMatch:
             context['add_url'] = None # Handle cases where add view doesn't exist

        # Add action buttons (e.g., 'add') if specified
        if hasattr(self, 'action_buttons') and 'add' in self.action_buttons:
            try: # --- Restore TRY/EXCEPT block ---
                 # Correctly generate the create URL name using the determined _model
                create_url_name = get_model_viewname(_model, 'create') 
                # Try resolving it to ensure it exists before adding to context
                reverse(create_url_name) # Check if URL can be reversed
                context['create_url_name'] = create_url_name
            except NoReverseMatch:
                # If the create URL doesn't exist, don't add it to context
                pass 
            # --- END Restore TRY/EXCEPT block ---

        # Get pagination context
        if self.paginate_by:
            context['paginate_by'] = self.paginate_by

        # Add other common context like permissions, bulk action form etc.
        # print(f"[DEBUG] ObjectListView context for {_model}: model_name_str = {context.get('model_name_str')}") # Remove DEBUG print
        return context

class ObjectDetailView(LoginRequiredMixin, DetailView):
    """Base view for displaying a single object."""
    template_name = 'generic/object_detail.html' # Default template - corrected name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add common detail view context: permissions, related objects, changelog etc.
        obj = self.get_object()
        app_label = obj._meta.app_label
        model_name = obj._meta.model_name
        # Permissions
        context['can_change'] = self.request.user.has_perm(f'{app_label}.change_{model_name}')
        context['can_delete'] = self.request.user.has_perm(f'{app_label}.delete_{model_name}')
        context['edit_url'] = reverse(f'{app_label}:{model_name}_update', kwargs={'pk': obj.pk})
        context['delete_url'] = reverse(f'{app_label}:{model_name}_delete', kwargs={'pk': obj.pk})
        # Changelog (if using ChangeLoggingMixin)
        if hasattr(obj, 'get_changelog_url'): # Assumes method exists
            context['changelog_url'] = obj.get_changelog_url()
        elif ContentType.objects.filter(app_label='core', model='objectchange').exists():
            # Generic changelog link based on ContentType
            obj_type = ContentType.objects.get_for_model(obj)
            changelog_url = reverse('objectchange_list') + '?' + urlencode({'changed_object_type': obj_type.pk, 'changed_object_id': obj.pk})
            context['changelog_url'] = changelog_url

        # Add related object panels/tabs here later
        return context

    # --- Add HTMX handling for Detail View --- 
    def render_to_response(self, context, **response_kwargs):
        if self.request.htmx:
            # If HTMX, render the partial wrapper designed for detail pages
            print(f"[DEBUG] HTMX request detected (Target: {self.request.headers.get('HX-Target', 'N/A')}): Rendering partial htmx_detail_page_wrapper.html") # DEBUG
            context['request'] = self.request # Ensure request is in context
            # Include object_type if needed by the partial's OOB blocks
            context['object_type'] = self.object._meta.verbose_name 
            # Include action_urls if needed by the partial's OOB blocks
            app_label = self.object._meta.app_label
            model_name = self.object._meta.model_name
            context['action_urls'] = {
                'edit': reverse(f'{app_label}:{model_name}_update', kwargs={'pk': self.object.pk}) if self.request.user.has_perm(f'{app_label}.change_{model_name}') else None,
                'delete': reverse(f'{app_label}:{model_name}_delete', kwargs={'pk': self.object.pk}) if self.request.user.has_perm(f'{app_label}.delete_{model_name}') else None,
            }
            # Pass any other necessary context for OOB blocks here...
            return render(
                self.request,
                'generic/partials/htmx_detail_page_wrapper.html',
                context
            )
        
        # Standard rendering for non-HTMX requests
        return super().render_to_response(context, **response_kwargs)
    # --- End HTMX Handling --- 

class ObjectEditView(LoginRequiredMixin, UpdateView):
    """Base view for creating or editing an object."""
    model_form = None # Subclasses must specify form class
    template_name = 'generic/object_edit.html' # Default template
    # default_return_url can be set by subclasses

    def get_form_class(self):
        if self.model_form:
            return self.model_form
        return super().get_form_class()
    
    def get_object(self, queryset=None):
        # Handle create view (pk not present in URL)
        if 'pk' not in self.kwargs:
            return None
        return super().get_object(queryset)

    def get_form(self, form_class=None):
        """Instantiate the form with the object instance."""
        kwargs = self.get_form_kwargs()
        # For create views, instance will be None
        kwargs['instance'] = self.object
        if form_class is None:
            form_class = self.get_form_class()
        return form_class(**kwargs)

    def get_success_url(self):
        # Priority: POST param -> default_return_url -> object.get_absolute_url() -> list view
        if self.request.POST.get('return_url'):
            return self.request.POST.get('return_url')
        if hasattr(self, 'default_return_url') and self.default_return_url:
            return reverse(self.default_return_url)
        if self.object and hasattr(self.object, 'get_absolute_url'):
            return self.object.get_absolute_url()
        # Fallback to list view
        list_view_name = get_model_viewname(self.model, 'list') # Use helper
        return reverse(list_view_name)

    def form_valid(self, form):
        is_creating = self.object is None # self.object is None when creating
        self.object = form.save() # self.object is now set
        # Get model from the form
        _model = form._meta.model 
        # Use _model instead of self.model
        msg = f"{'Created' if is_creating else 'Modified'} {_model._meta.verbose_name} "
        msg += f"<a href='{self.object.get_absolute_url()}'>{self.object}</a>" if hasattr(self.object, 'get_absolute_url') else f"{self.object}"
        messages.success(self.request, msg)
        
        # Handle different save buttons
        if self.request.POST.get('_addanother'):
            # Use _model here too
            add_view_name = get_model_viewname(_model, 'add') 
            return redirect(reverse(add_view_name))
        elif self.request.POST.get('_continue'):
            # Use _model here too
            edit_view_name = get_model_viewname(_model, 'edit') 
            return redirect(reverse(edit_view_name, kwargs={'pk': self.object.pk}))
            
        return HttpResponseRedirect(self.get_success_url()) # Default redirect

    def get_context_data(self, **kwargs):
        # Determine the model class reliably
        _model = getattr(self, 'model', None)
        if not _model and hasattr(self, 'queryset') and self.queryset is not None:
            _model = self.queryset.model
        elif not _model and hasattr(self, 'model_form') and self.model_form is not None:
             _model = self.model_form._meta.model
        elif not _model and hasattr(self, 'form_class') and self.form_class is not None:
            # Fallback to form_class if model_form isn't used directly
            if hasattr(self.form_class, '_meta') and hasattr(self.form_class._meta, 'model'):
                _model = self.form_class._meta.model

        if not _model:
             raise ImproperlyConfigured(
                 f"{self.__class__.__name__} needs a model attribute, or a queryset/model_form with a model."
             )

        context = super().get_context_data(**kwargs)
        context['model'] = _model # Use determined model
        context['verbose_name'] = _model._meta.verbose_name
        context['is_editing'] = self.object is not None
        
        # Define cancel URL using the determined model
        if self.object and hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            try:
                 list_view_name = get_model_viewname(_model, 'list') # Use determined model
                 context['cancel_url'] = reverse(list_view_name)
            except NoReverseMatch:
                 context['cancel_url'] = reverse('dashboard') # Sensible fallback
        return context

class ObjectDeleteView(LoginRequiredMixin, DeleteView):
    """Base view for deleting an object."""
    template_name = 'generic/object_delete.html' # Default confirmation template
    # default_return_url can be set by subclasses
    form_class = ConfirmationForm # Generic confirmation form

    def get_success_url(self):
        # Priority: POST param -> default_return_url -> list view
        if self.request.POST.get('return_url'):
            return self.request.POST.get('return_url')
        if hasattr(self, 'default_return_url') and self.default_return_url:
            return reverse(self.default_return_url)
        # Fallback to list view
        list_view_name = get_model_viewname(self.model, 'list')
        return reverse(list_view_name)

    def get_form_kwargs(self):
        # Pass the object to the confirmation form
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.object
        return kwargs

    def form_valid(self, form):
        """Handle object deletion and potential ProtectedError."""
        obj_repr = str(self.object)
        try:
            self.object.delete()
            messages.success(self.request, f"Deleted {self.model._meta.verbose_name} {obj_repr}.")
            return HttpResponseRedirect(self.get_success_url())
        except ProtectedError as e:
            messages.error(self.request, f"Unable to delete {obj_repr}. Objects are protected: {e}")
            # Redirect back to the object's detail view if possible, else list view
            if hasattr(self.object, 'get_absolute_url'):
                return redirect(self.object.get_absolute_url())
            return redirect(self.get_success_url()) # Redirect to list/default

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['model'] = self.model
        context['verbose_name'] = self.model._meta.verbose_name
        # Define cancel URL - usually the object's detail view
        if hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            # Fallback if no detail view (unlikely for deletable objects)
            list_view_name = get_model_viewname(self.model, 'list')
            context['cancel_url'] = reverse(list_view_name)
        # Pass return_url if provided in GET params
        context['return_url'] = self.request.GET.get('return_url', context['cancel_url'])
        return context 