# assetbox/core/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.apps import apps # To find models/tables dynamically
from django.urls import reverse
from importlib import import_module
from django.http import Http404

from .models import UserPreference # Import the model
from .forms import TableConfigForm
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