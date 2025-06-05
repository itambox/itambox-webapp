# assetbox/core/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.apps import apps # To find models/tables dynamically
from django.urls import reverse
from importlib import import_module

from .models import UserPreference # Import the model
from .forms import TableConfigForm
# from .tables.base import SESSION_KEY_PREFIX # No longer needed

@login_required
def table_config(request, model_name):
    """
    View for configuring table columns via a modal form.
    Saves preferences to UserPreference model.
    """
    # Dynamically find the Table class based on model_name
    # (Assumes a naming convention: AppLabel.ModelName -> AppLabelTable in app.tables)
    app_label, model_lower = model_name.split('.')
    model = apps.get_model(app_label, model_lower)
    table_class_name = f"{model.__name__}Table"
    try:
        tables_module = import_module(f'{app_label}.tables')
        table_class = getattr(tables_module, table_class_name)
    except (ModuleNotFoundError, AttributeError):
        # Handle error: Table class not found
        # (render an error message or redirect)
        # For now, just return an empty response or raise Http404
        from django.http import Http404
        raise Http404(f"Table class {table_class_name} not found for {model_name}")

    # Instantiate the table (only need structure, no data)
    table = table_class(data=[], request=request)

    # Get or create UserPreference for the current user
    prefs, _ = UserPreference.objects.get_or_create(user=request.user)

    # Key for storing this table's config within the JSONField
    table_key = f"{model_name}" # e.g., "assets.asset"

    # Get current config for this table from preferences
    user_config = prefs.data.get('tables', {}).get(table_key, {})
    print(f"[table_config GET] User config for {table_key}: {user_config}") # DEBUG

    if request.method == 'POST':
        print(f"[table_config] Entered POST block for user {request.user}. Table key: {table_key}") # DEBUG
        print(f"[table_config POST] Raw request.POST: {request.POST}") # DEBUG Raw POST data
        # Initialize form only with table structure and POST data for cleaning
        form = TableConfigForm(table=table, data=request.POST)
        if form.is_valid():
            # Update the preferences data safely
            if 'tables' not in prefs.data or not isinstance(prefs.data.get('tables'), dict):
                prefs.data['tables'] = {} # Ensure 'tables' key exists and is a dict
            
            table_prefs = prefs.data['tables'].get(table_key, {}) # Get existing or empty dict for this table
            table_prefs['columns'] = form.cleaned_data['columns'] # Update/set columns
            
            prefs.data['tables'][table_key] = table_prefs # Put it back into the main data dict
            
            print(f"[table_config POST] Preparing to save preferences for {request.user}. Table key: {table_key}. Data: {prefs.data}") # DEBUG before save
            prefs.save()
            # Verify data after save (optional but good for debugging)
            prefs.refresh_from_db()
            print(f"[table_config POST] Verified saved preferences for {request.user}: {prefs.data}") # DEBUG after save

            # Send HX-Refresh header to trigger page reload
            response = HttpResponse("")
            response['HX-Refresh'] = 'true'
            return response
        else:
             print(f"[table_config POST] Form invalid for user {request.user}. Errors: {form.errors}") # DEBUG form errors
        # If form is invalid, fall through to render the form with errors

    else: # GET request
        form = TableConfigForm(table=table, user_config=user_config)

    context = {
        'form': form,
        'table_name': table_key, # Pass the key used in prefs
        'table_verbose_name': model._meta.verbose_name_plural.title(),
    }
    return render(request, 'core/partials/table_config_modal.html', context) 