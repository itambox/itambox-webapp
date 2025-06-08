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

from .models import UserPreference, ObjectChange # Import the model
from .forms import TableConfigForm
from .tables import ObjectChangeTable
# from .tables.base import SESSION_KEY_PREFIX # No longer needed

@login_required
def table_config(request, model_name):
    """
    View to render the table configuration modal form.
    Saving/Resetting is handled via API and JavaScript.
    """
    # Dynamically find the Table class
    app_label, model_lower = model_name.split('.')
    model = apps.get_model(app_label, model_lower)
    table_class_name = f"{model.__name__}Table"
    try:
        tables_module = import_module(f'{app_label}.tables')
        table_class = getattr(tables_module, table_class_name)
    except (ModuleNotFoundError, AttributeError):
        raise Http404(f"Table class {table_class_name} not found for {model_name}")

    # Instantiate the table (only need structure, no data)
    table = table_class(data=[], request=request)

    # Get or create UserPreference for the current user
    prefs, _ = UserPreference.objects.get_or_create(user=request.user)

    # Key for storing this table's config within the JSONField
    table_key = f"tables.{model_name}" # Use dot notation for nested access

    # Get current config for this table from preferences
    # Simplified access assuming prefs.data = {"tables": {"app.model": {...}}}
    user_config = prefs.data.get('tables', {}).get(model_name, {}) # Get config for this specific table

    # POST is no longer handled here
    # Always initialize form with table structure and user config
    form = TableConfigForm(table=table, user_config=user_config)

    context = {
        'form': form,
        'table_name': form.table_name, # Pass table name for JS/API
        'table_verbose_name': model._meta.verbose_name_plural.title(),
    }
    # Render the specific modal partial
    return render(request, 'core/includes/table_config_modal.html', context)

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
    template_name = 'core/objectchange_list.html'
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
    template_name = 'core/objectchange.html'

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