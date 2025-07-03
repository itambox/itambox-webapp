# assetbox/core/views.py
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.apps import apps # To find models/tables dynamically
from django.urls import reverse
from importlib import import_module
from django.http import Http404
from django.views.generic import DetailView
from django_tables2 import SingleTableView, RequestConfig
from django.utils.decorators import method_decorator
import json
from django.core.serializers.json import DjangoJSONEncoder
import difflib
from django.conf import settings # Add settings
from django.contrib.auth import get_user_model # Import get_user_model
from users.forms import TableConfigForm # Correct import
from users.models import UserPreference # Import UserPreference from users
from django.template.loader import get_template # Import get_template
from django.template import TemplateDoesNotExist

logger = logging.getLogger(__name__)

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
from django.core.exceptions import ImproperlyConfigured # Added ImproperlyConfigured

User = get_user_model() # Get the User model

# =============================================================================
# NEW Base HTMX View
# =============================================================================
class BaseHTMXView: # No Django View inheritance needed here, it's a mixin
    """
    Mixin to handle HTMX request rendering, specifically for boosted main-body swaps.
    Views using this should ensure their templates do *not* extend base.html.
    They should also define breadcrumbs and title in get_context_data.
    """
    page_body_partial_name = "generic/partials/page_body_content_wrapper.html" # Partial for #page-body-main swaps (hx-boost)

    def get_template_names(self):
        # Ensure subclasses provide template_name or delegate to superclass inference
        if not hasattr(self, 'template_name') or not self.template_name:
            if hasattr(super(), 'get_template_names'):
                return super().get_template_names()
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} needs a template_name attribute defined."
            )
        return [self.template_name]

    def render_to_response(self, context, **response_kwargs):
        """
        Override render_to_response to handle HTMX requests.
        - Sets dynamic base template to base_htmx.html for boosted main-body swaps.
        - Falls back to standard rendering otherwise.
        """
        request = self.request # Get request from the view instance

        if getattr(request, 'htmx', False):
            context['request'] = request # Ensure request is always in context for templates

            target = getattr(request.htmx, 'target', '') or ''
            is_boosted_main_swap = getattr(request.htmx, 'boosted', False) or \
                                   target in ('page-content-wrapper', '#page-content-wrapper', 'page-body-main', '#page-body-main')

            if is_boosted_main_swap:
                # Dynamic base template swap
                context['base_template'] = 'base_htmx.html'
                # Ensure common context variables expected by base_htmx.html are present
                context.setdefault('title', 'AssetBox') # Provide a default title
                context.setdefault('breadcrumbs', [(reverse('dashboard'), 'Dashboard'), (None, context['title'])])
                context.setdefault('page_actions', None) # e.g., buttons, passed via context

        # Standard rendering for non-HTMX requests or non-main-body swaps
        # This relies on the base Django view's render_to_response (e.g., TemplateView.render_to_response)
        # Ensure the method we call exists in the MRO
        if hasattr(super(), 'render_to_response'):
            return super().render_to_response(context, **response_kwargs)
        else:
            # Fallback if super().render_to_response is not available (e.g., mixing with raw View)
            # This requires TemplateResponseMixin or similar to be in the MRO
            if hasattr(self, 'response_class') and hasattr(self, 'get_template_names'):
                 return self.response_class(
                    request=request,
                    template=self.get_template_names(),
                    context=context,
                    using=self.template_engine,
                    **response_kwargs
                )
            else:
                raise ImproperlyConfigured(f"{self.__class__.__name__} or its superclasses must provide a render_to_response method or be mixed with TemplateResponseMixin.")


# =============================================================================
# Generic Object Views (NetBox-inspired base classes)
# =============================================================================

class ObjectListView(LoginRequiredMixin, ListView):
    """Base view for listing objects."""
    filterset = None
    filterset_form = None
    table = None
    template_name = 'generic/object_list.html' # Default template (Full page)
    list_content_partial_name = "generic/partials/htmx_list_page_wrapper.html" # Partial for #object-list-dynamic-content swaps
    page_body_partial_name = "generic/partials/page_body_content_wrapper.html" # Partial for #page-body-main swaps (hx-boost)
    action_buttons = () # Tuple of actions ('add', 'import', 'export') - Default empty

    # --- Add render_to_response to handle HTMX requests ---
    def render_to_response(self, context, **response_kwargs):
        """
        Override render_to_response to handle HTMX requests.
        - Sets dynamic base template to base_htmx.html for boosted main-body swaps.
        - Renders list content partial for specific content updates (pagination, filters).
        """
        if self.request.htmx:
            context['request'] = self.request # Ensure request is always in context for HTMX partials/templates
            
            target = getattr(self.request.htmx, 'target', '') or ''
            is_boosted_main_swap = self.request.htmx.boosted or \
                                   target in ('page-content-wrapper', '#page-content-wrapper', 'page-body-main', '#page-body-main')

            if is_boosted_main_swap:
                 context['base_template'] = 'base_htmx.html'
                 return super().render_to_response(context, **response_kwargs)
            else:
                 # Render ONLY the LIST CONTENT partial for specific dynamic content swaps
                 # (e.g., pagination, filtering targeting #object-list-dynamic-content).
                 # This wrapper handles OOB swaps for elements outside the dynamic content.
                 return render(self.request, self.list_content_partial_name, context)

        # Standard rendering for non-HTMX requests (renders self.template_name)
        return super().render_to_response(context, **response_kwargs)
    # --- End render_to_response ---

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.filterset:
            self.filter = self.filterset(self.request.GET, queryset)
            if not self.filter.is_valid():
                # Log invalid filter params but still return unfiltered queryset
                # to avoid a blank page. In production, this should be monitored.
                import logging
                logger = logging.getLogger(__name__)
                logger.warning('Invalid filter params for %s: %s', self.__class__.__name__, self.filter.errors)
                self.filter = None  # prevent further invalid access
            else:
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
        return table

    def get_context_data(self, **kwargs):
        _model = getattr(self, 'model', None)
        if not _model and hasattr(self, 'queryset') and self.queryset is not None:
            _model = self.queryset.model
        elif not _model and hasattr(self, 'object_list') and self.object_list is not None:
            _model = self.object_list.model
        
        if not _model:
             raise ImproperlyConfigured(
                 f"{self.__class__.__name__} is missing a QuerySet. Define "
                 f"{self.__class__.__name__}.model, {self.__class__.__name__}.queryset, or override "
                 f"{self.__class__.__name__}.get_queryset()."
             )

        context = super().get_context_data(**kwargs)
        self.model = _model # Ensure self.model is set for get_table

        table = self.get_table()
        # Apply pagination via RequestConfig (critical for list view performance)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(table)
        filter_form = self.filterset_form(self.request.GET) if self.filterset_form else None
        context['table'] = table
        context['filter_form'] = filter_form
        context['model'] = _model
        context['verbose_name_plural'] = _model._meta.verbose_name_plural
        context['model_name_str'] = f"{_model._meta.app_label}.{_model._meta.model_name}"
        context['table_config_key'] = f"{_model._meta.app_label}.{table.__class__.__name__}"

        # Set default title based on model verbose name plural
        context.setdefault('title', _model._meta.verbose_name_plural.title())

        # Generate create URL name based on convention
        try:
            create_url_name = get_model_viewname(_model, 'create')
            reverse(create_url_name) # Check if it exists
            context['create_url_name'] = create_url_name
        except NoReverseMatch:
            context['create_url_name'] = None

        # Add action buttons to context IF they are defined in the tuple AND the corresponding URL exists
        context['action_buttons'] = self.action_buttons # Pass the tuple itself
        # Example: Check if 'add' is requested and if the create_url actually exists
        if 'add' in self.action_buttons and not context['create_url_name']:
             # Remove 'add' if URL doesn't exist to prevent button rendering issues
             # This assumes action_buttons is mutable; might need list conversion if tuple
             pass # Or handle more explicitly if needed
        # Similar checks for import/export if implemented

        # Breadcrumbs
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (None, context['title']) # Use title from context
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        return context

class ObjectDetailView(LoginRequiredMixin, BaseHTMXView, DetailView):
    """Base view for displaying a single object."""
    template_name = 'generic/object_detail.html'
    detail_page_body_partial_name = "generic/partials/htmx_detail_page_wrapper.html" # Specific wrapper for detail views

    def get_template_names(self):
        # If a specific template_name is defined on the subclass (i.e. not 'generic/object_detail.html')
        if self.template_name and self.template_name != 'generic/object_detail.html':
            return [self.template_name]

        # Otherwise, dynamically search by convention
        obj = self.get_object()
        if obj:
            app_label = obj._meta.app_label
            model_name = obj._meta.model_name
            # Plural name (e.g. assetroles, assets)
            plural_name = obj._meta.verbose_name_plural.lower().replace(" ", "")

            templates_to_try = [
                f"{app_label}/{plural_name}/{model_name}_detail.html",
                f"{app_label}/{model_name}_detail.html",
                'generic/object_detail.html'
            ]

            for template_name in templates_to_try:
                try:
                    get_template(template_name)
                    return [template_name]
                except TemplateDoesNotExist:
                    continue

        return ['generic/object_detail.html']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        app_label = obj._meta.app_label
        model_name = obj._meta.model_name
        verbose_name = obj._meta.verbose_name.title()
        verbose_name_plural = obj._meta.verbose_name_plural.title()

        # Permissions & URLs
        can_change = self.request.user.has_perm(f'{app_label}.change_{model_name}')
        can_delete = self.request.user.has_perm(f'{app_label}.delete_{model_name}')
        context['can_change'] = can_change
        context['can_delete'] = can_delete
        context['edit_url'] = None
        if can_change:
            try:
                context['edit_url'] = reverse(f'{app_label}:{model_name}_update', kwargs={'pk': obj.pk})
            except NoReverseMatch:
                if hasattr(obj, 'slug') and obj.slug:
                    try:
                        context['edit_url'] = reverse(f'{app_label}:{model_name}_update', kwargs={'slug': obj.slug})
                    except NoReverseMatch:
                        pass
        
        context['delete_url'] = None
        if can_delete:
            try:
                context['delete_url'] = reverse(f'{app_label}:{model_name}_delete', kwargs={'pk': obj.pk})
            except NoReverseMatch:
                if hasattr(obj, 'slug') and obj.slug:
                    try:
                        context['delete_url'] = reverse(f'{app_label}:{model_name}_delete', kwargs={'slug': obj.slug})
                    except NoReverseMatch:
                        pass
        
        # Title and Breadcrumbs
        context['title'] = str(obj) # Default title is the object string representation
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (reverse(get_model_viewname(obj, 'list')), verbose_name_plural), # Link to list view
            (None, context['title']) # Current object
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()

        # Changelog URL
        if hasattr(obj, 'get_changelog_url'):
            context['changelog_url'] = obj.get_changelog_url()
        elif ContentType.objects.filter(app_label='core', model='objectchange').exists():
            obj_type = ContentType.objects.get_for_model(obj)
            changelog_url = reverse('objectchange_list') + '?' + urlencode({'changed_object_type': obj_type.pk, 'changed_object_id': obj.pk})
            context['changelog_url'] = changelog_url

        # Build dynamic changelog_table for all models
        if ContentType.objects.filter(app_label='core', model='objectchange').exists():
            obj_type = ContentType.objects.get_for_model(obj)
            changelog_qs = ObjectChange.objects.filter(
                changed_object_type=obj_type,
                changed_object_id=obj.pk
            ).order_by('-time')[:50]
            changelog_table = ObjectChangeTable(list(changelog_qs))
            RequestConfig(self.request, paginate={'per_page': 10}).configure(changelog_table)
            context['changelog_table'] = changelog_table

        # Action buttons/URLs for the wrapper template
        context['page_actions'] = {
            'edit_url': context.get('edit_url'),
            'delete_url': context.get('delete_url'),
        }
        context['action_urls'] = {
            'edit': context.get('edit_url'),
            'delete': context.get('delete_url'),
        }
        # Pass the main template name to the wrapper
        context['content_template_name'] = self.get_template_names()[0]

        return context

class ObjectEditView(LoginRequiredMixin, BaseHTMXView, UpdateView):
    """Base view for creating or editing an object."""
    model_form = None
    template_name = 'generic/object_edit.html'

    def _get_model(self):
        # Helper to reliably get model class
        if hasattr(self, 'model') and self.model:
            return self.model
        if hasattr(self, 'queryset') and self.queryset is not None:
            return self.queryset.model
        if hasattr(self, 'model_form') and self.model_form:
             return self.model_form._meta.model
        if hasattr(self, 'form_class') and self.form_class and hasattr(self.form_class, '_meta'):
            return self.form_class._meta.model
        return None

    def get_form_class(self):
        if self.model_form:
            return self.model_form
        return super().get_form_class()
    
    def get_object(self, queryset=None):
        if 'pk' not in self.kwargs and 'slug' not in self.kwargs:
            return None # Create view
        return super().get_object(queryset)

    def get_form(self, form_class=None):
        kwargs = self.get_form_kwargs()
        kwargs['instance'] = self.object
        if form_class is None:
            form_class = self.get_form_class()
        return form_class(**kwargs)

    def get_success_url(self):
        if self.request.POST.get('return_url'):
            return self.request.POST.get('return_url')
        if hasattr(self, 'default_return_url') and self.default_return_url:
            return reverse(self.default_return_url)
        if self.object and hasattr(self.object, 'get_absolute_url'):
            return self.object.get_absolute_url()
        # Fallback to list view
        _model = self._get_model()
        if _model:
            try:
                list_view_name = get_model_viewname(_model, 'list')
                return reverse(list_view_name)
            except NoReverseMatch:
                pass
        return reverse('dashboard') # Ultimate fallback

    def form_valid(self, form):
        is_creating = self.object is None
        _model = self._get_model()
        self.object = form.save()
        msg_verb = 'Created' if is_creating else 'Modified'
        msg_link = f"<a href='{self.object.get_absolute_url()}'>{self.object}</a>" if hasattr(self.object, 'get_absolute_url') else str(self.object)
        messages.success(self.request, f"{msg_verb} {_model._meta.verbose_name} {msg_link}")
        
        if self.request.POST.get('_addanother') and _model:
            try:
                add_view_name = get_model_viewname(_model, 'add') 
                return redirect(reverse(add_view_name))
            except NoReverseMatch:
                pass # Fall through to default redirect
        elif self.request.POST.get('_continue') and _model:
            try:
                edit_view_name = get_model_viewname(_model, 'edit') 
                try:
                    return redirect(reverse(edit_view_name, kwargs={'pk': self.object.pk}))
                except NoReverseMatch:
                    if hasattr(self.object, 'slug') and self.object.slug:
                        return redirect(reverse(edit_view_name, kwargs={'slug': self.object.slug}))
                    raise
            except NoReverseMatch:
                pass # Fall through to default redirect
            
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _model = self._get_model()
        if not _model:
             raise ImproperlyConfigured(f"{self.__class__.__name__} needs a model attribute, or related form/queryset.")

        is_editing = self.object is not None
        context['model'] = _model
        context['verbose_name'] = _model._meta.verbose_name
        context['is_editing'] = is_editing
        context['title'] = f"{'Edit' if is_editing else 'Create'} {context['verbose_name']}"

        # Cancel URL
        if self.object and hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            try:
                 list_view_name = get_model_viewname(_model, 'list')
                 context['cancel_url'] = reverse(list_view_name)
            except NoReverseMatch:
                 context['cancel_url'] = reverse('dashboard')
        
        # Breadcrumbs
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (context['cancel_url'], _model._meta.verbose_name_plural.title()), # Link to List or Detail
            (None, context['title']) # Current action
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        return context

    # render_to_response is now handled by BaseHTMXView

class ObjectDeleteView(LoginRequiredMixin, BaseHTMXView, DeleteView):
    """Base view for deleting an object."""
    template_name = 'generic/object_confirm_delete.html'
    form_class = ConfirmationForm

    def get_success_url(self):
        if self.request.POST.get('return_url'):
            return self.request.POST.get('return_url')
        if hasattr(self, 'default_return_url') and self.default_return_url:
            return reverse(self.default_return_url)
        # Fallback to list view
        try:
            list_view_name = get_model_viewname(self.model, 'list')
            return reverse(list_view_name)
        except NoReverseMatch:
            return reverse('dashboard') # Fallback

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.object
        return kwargs

    def form_valid(self, form):
        obj_repr = str(self.object)
        model = self.object.__class__
        try:
            self.object.delete()
            messages.success(self.request, f"Deleted {model._meta.verbose_name} {obj_repr}.")
            return HttpResponseRedirect(self.get_success_url())
        except ProtectedError as e:
            messages.error(self.request, f"Unable to delete {obj_repr}. Objects are protected: {e}")
            if hasattr(self.object, 'get_absolute_url'):
                return redirect(self.object.get_absolute_url())
            return redirect(self.get_success_url()) # Redirect to list/default

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        model = self.object.__class__ if self.object else (self.model or None)
        if model is None:
            raise ValueError("Cannot determine model for delete view.")
        context['model'] = self.model or model
        context['verbose_name'] = model._meta.verbose_name
        context['title'] = f"Delete {context['verbose_name']}: {self.object}"
        
        # Cancel URL
        if hasattr(self.object, 'get_absolute_url'):
            context['cancel_url'] = self.object.get_absolute_url()
        else:
            try:
                list_view_name = get_model_viewname(model, 'list')
                context['cancel_url'] = reverse(list_view_name)
            except NoReverseMatch:
                context['cancel_url'] = reverse('dashboard')
        context['return_url'] = self.request.GET.get('return_url', context['cancel_url'])
        
        # Breadcrumbs
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (context['cancel_url'], model._meta.verbose_name_plural.title()), # Link to List or Detail
            (None, f"Delete {self.object}") # Current action
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        return context 

    # render_to_response is now handled by BaseHTMXView

# =============================================================================
# END Generic Object Views
# =============================================================================


# =============================================================================
# Specific Core Views (e.g., Changelog, Search, Config)
# =============================================================================

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
    logger.debug("Fetched user_config for %s.%s: %s", app_label, table_part, user_config)
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


@method_decorator(login_required, name='dispatch')
class ObjectChangeListView(ObjectListView): # Inherit from ObjectListView
    queryset = ObjectChange.objects.prefetch_related(
        'user', 'changed_object_type', 'related_object_type'
    )
    table = ObjectChangeTable # Use 'table' attribute
    template_name = 'core/objectchange/objectchange_list.html' # Keep specific template
    action_buttons = () # Read-only view
    # filterset = ObjectChangeFilterSet # Comment out - FilterSet not yet defined
    # filterset_form = ObjectChangeFilterForm # Comment out - FilterForm not yet defined

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (None, 'Changelog') # Current page
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Changelog' # Set specific title
        return context


@method_decorator(login_required, name='dispatch')
class ObjectChangeView(BaseHTMXView, DetailView):
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

        # --- Add Breadcrumbs --- 
        obj = self.get_object()
        context['title'] = f"Change #{obj.pk}" # Set title
        base_breadcrumbs = [
            (reverse('dashboard'), 'Dashboard'),
            (reverse('objectchange_list'), 'Changelog'), # Link to list view
            (None, context['title']) # Current change
        ]
        context['breadcrumbs'] = getattr(self, 'get_breadcrumbs', lambda: base_breadcrumbs)()
        # --- End Breadcrumbs --- 
        # Add content_template_name for the wrapper
        context['content_template_name'] = self.template_name
        return context

    # render_to_response is handled by BaseHTMXView


# --- Search View ---
class SearchView(LoginRequiredMixin, BaseHTMXView, View):
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
                    # Add list URL using view name helper
                    try:
                        from django.urls import reverse
                        from core.utils import get_model_viewname
                        data['list_url'] = reverse(get_model_viewname(model, 'list'))
                    except Exception:
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
            'title': 'Search Results',
            'breadcrumbs': [
                 (reverse('dashboard'), 'Dashboard'),
                 (None, 'Search')
            ]
        }
        # Manually call render_to_response logic for View base class
        context['content_template_name'] = self.template_name # Needed by BaseHTMXView
        return self.render_to_response(context) # Call the mixin's method

    # Define render_to_response to bridge View and BaseHTMXView
    def render_to_response(self, context, **response_kwargs):
        # Leverage the mixin's render_to_response
        return BaseHTMXView.render_to_response(self, context, **response_kwargs)


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
        from django.db import transaction
        try:
            count = len(objects_to_delete)
            with transaction.atomic():
                for obj in objects_to_delete:
                    obj.delete() # Triggers ChangeLoggingMixin audit logging
            messages.success(request, f"Successfully deleted {count} {model._meta.verbose_name_plural}.")
            return redirect(return_url)
        except ProtectedError as e:
            # Handle protected objects
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

# ... (Any other User/Profile/etc views remain at the end) ... 