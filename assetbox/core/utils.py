from .constants import DEFAULT_PAGINATE_COUNT, PAGINATE_COUNT_CHOICES
import datetime # Import datetime
from django.conf import settings
from django.core.paginator import Paginator
from django.contrib.contenttypes.models import ContentType # Import ContentType
from django.utils.module_loading import import_string # Import import_string
from django.shortcuts import reverse
from users.models import UserPreference # Import UserPreference

def get_model_viewname(model, action):
    """
    Return the conventional view name for the given model and action.
    Example: (Asset, 'list') -> 'assets:asset_list'
    """
    app_label = model._meta.app_label
    model_name = model._meta.model_name
    return f"{app_label}:{model_name}_{action}"

def get_paginate_count(request):
    """
    Determine the number of objects to display per page.
    Checks request query parameters first, then user preferences, then default.
    """
    # 1. Check for per_page query parameter
    try:
        per_page_param = request.GET.get('per_page')
        if per_page_param:
            per_page = int(per_page_param)
            # Validate against choices
            if per_page in dict(PAGINATE_COUNT_CHOICES):
                # print(f"[get_paginate_count] Using per_page from query param: {per_page}") # DEBUG
                return per_page
    except (ValueError, TypeError): # Catch potential int conversion error or None
        pass

    # 2. Check user preferences (if logged in)
    if request.user.is_authenticated:
        try:
            # Use filter().first() to avoid DoesNotExist exception if no prefs exist yet
            prefs = UserPreference.objects.filter(user=request.user).first()
            if prefs and prefs.data:
                user_pref_val = prefs.data.get('pagination', {}).get('per_page')
                if user_pref_val:
                    try: 
                        user_pref = int(user_pref_val) # Ensure it's an integer
                        if user_pref in dict(PAGINATE_COUNT_CHOICES):
                            # print(f"[get_paginate_count] Using per_page from user prefs: {user_pref}") # DEBUG
                            return user_pref
                    except (ValueError, TypeError):
                         pass # Ignore invalid preference value
        except Exception as e:
            # Log error maybe? For now, just ignore preference errors and fallback
            # print(f"[get_paginate_count] Error checking user prefs: {e}") # DEBUG
            pass

    # 3. Fallback to default
    # print(f"[get_paginate_count] Using default per_page: {DEFAULT_PAGINATE_COUNT}") # DEBUG
    return DEFAULT_PAGINATE_COUNT

# Simple base class placeholder for ChoiceSets
# Subclasses should define CHOICES = ( (value, label, color), ... )
# and constants like ACTION_CREATE = 'create'
class ChoiceSet:
    CHOICES = [] # Subclasses must define CHOICES

    def __iter__(self):
        # Yield only the (value, label) pairs for Django's choices argument
        yield from [(c[0], c[1]) for c in self.CHOICES]

    # Add other helper methods here later if needed (e.g., for colors)
    pass 

from django.contrib.contenttypes.models import ContentType
from django.forms.models import model_to_dict
from .middleware import get_current_request_id, get_current_user

def serialize_object(obj, extra_data=None, exclude_fields=None):
    """
    Serialize a model instance into a dictionary, suitable for storing in ObjectChange.
    Similar to NetBox's approach.
    """
    data = model_to_dict(obj, exclude=exclude_fields)
    # Add any M2M fields or other related data if needed
    # ... (implementation depends on specific model needs)

    # Include any extra data provided
    if extra_data:
        data.update(extra_data)

    # Convert objects to JSON-serializable formats
    for key, value in data.items():
        if hasattr(value, 'pk'): # Handle related objects (convert to PK)
            data[key] = getattr(value, 'pk')
        elif isinstance(value, (datetime.date, datetime.datetime)): # Convert date/datetime to ISO string
            data[key] = value.isoformat()
        # Add more type conversions if needed (e.g., Decimal to string)

    return data

def log_change(instance, action, prechange_data=None, postchange_data=None, user=None, request_id=None):
    """
    Create an ObjectChange record for a model instance.

    :param instance: The model instance being changed.
    :param action: The action performed (create, update, delete).
    :param prechange_data: Optional dictionary representing the object state before the change.
    :param postchange_data: Optional dictionary representing the object state after the change.
    :param user: The user performing the change (defaults to user from middleware).
    :param request_id: The unique ID of the request (defaults to ID from middleware).
    """
    # Moved imports here to break circular dependencies
    from .choices import ObjectChangeActionChoices
    from .models import ObjectChange

    # Get user and request_id from middleware if not provided
    if user is None:
        user = get_current_user()
    if request_id is None:
        request_id = get_current_request_id()

    print(f"[LOG_CHANGE] User from middleware: {user}") # DEBUG
    print(f"[LOG_CHANGE] Request ID from middleware: {request_id}") # DEBUG

    # Ensure request_id is present (should always be set by middleware in a request)
    if not request_id:
        # Handle cases outside a request (e.g., management command, shell)
        # Or decide to raise an error if logging must happen within a request.
        # For now, let's skip logging if no request_id
        print(f"[LOG_CHANGE] Warning: Skipping changelog for {instance} ({action}) - no request_id found.") # DEBUG
        return

    try:
        print(f"[LOG_CHANGE] Attempting ObjectChange.objects.create for {instance}") # DEBUG
        oc = ObjectChange.objects.create(
            user=user,
            user_name=user.username if user else 'System', # Handle anonymous/system changes
            request_id=request_id,
            action=action,
            changed_object_type=ContentType.objects.get_for_model(instance),
            changed_object_id=instance.pk,
            object_repr=str(instance),
            # Use provided data or serialize on the fly (be careful with state)
            prechange_data=prechange_data,
            postchange_data=postchange_data
        )
        print(f"[LOG_CHANGE] ObjectChange created with PK: {oc.pk}") # DEBUG
        print(f"Changelog: Logged {action} for {instance} (User: {user}, Request: {request_id})") # Debug
    except Exception as e:
        # Handle potential errors during logging (e.g., database error)
        print(f"[LOG_CHANGE] Error logging change for {instance}: {e}") # DEBUG
        import traceback
        traceback.print_exc() # Print full traceback

# --- Add Helper Function --- 
def get_content_type_by_natural_key(natural_key):
    """
    Return a ContentType object based on its natural key string (e.g., 'app_label.model').
    Returns None if not found or invalid format.
    """
    try:
        app_label, model = natural_key.lower().split('.')
        return ContentType.objects.get(app_label=app_label, model=model)
    except (ContentType.DoesNotExist, ValueError, AttributeError):
        return None
# --- End Helper --- 

# --- Add get_table_for_model --- 
def get_table_for_model(model):
    """
    Return the django-tables2 Table class for the given model,
    or None if not found.
    Assumes table class name is ModelNameTable (e.g., Asset -> AssetTable).
    """
    app_label = model._meta.app_label
    model_name = model.__name__
    table_class_name = f"{model_name}Table"
    try:
        tables_module = import_string(f'{app_label}.tables')
        return getattr(tables_module, table_class_name)
    except (ImportError, AttributeError):
        # Log this? Could indicate missing tables.py or wrong naming convention
        print(f"[get_table_for_model] Warning: Could not find {table_class_name} in {app_label}.tables")
        return None
# --- End get_table_for_model ---

def get_model_from_string(model_string):
    """Resolve a model string like 'app_label.ModelName' to the actual model class."""
    try:
        app_label, model_name = model_string.split('.')
        return ContentType.objects.get(app_label=app_label, model=model_name.lower()).model_class()
    except (ContentType.DoesNotExist, ValueError):
        return None

# --- Breadcrumbs Functionality (Example) ---
def build_breadcrumbs(request, obj=None):
    breadcrumbs = [{'url': reverse('dashboard'), 'name': 'Home'}]
    path_parts = request.path.strip('/').split('/')

    # Simple example, needs refinement based on URL structure
    if len(path_parts) > 0 and path_parts[0]:
        # Assume first part is app/major section
        app_url_name = f"{path_parts[0]}:index" # Placeholder for potential app index
        try:
            # Attempt to generate a URL for the app/section index
            # This is highly dependent on your URL naming conventions
            # For 'assets', maybe we link to 'assets:asset_list'?
            if path_parts[0] == 'assets':
                 list_url = reverse('assets:asset_list') # Specific example
                 breadcrumbs.append({'url': list_url, 'name': path_parts[0].capitalize()})
            # Add more specific app logic here if needed
        except Exception: # Catch NoReverseMatch etc.
            breadcrumbs.append({'url': None, 'name': path_parts[0].capitalize()})

    if obj:
        # If an object is provided, add its list view and itself
        model_meta = obj._meta
        list_view_name = f"{model_meta.app_label}:{model_meta.model_name}_list"
        try:
            list_url = reverse(list_view_name)
            breadcrumbs.append({'url': list_url, 'name': model_meta.verbose_name_plural.capitalize()})
        except Exception:
            # Fallback if list view doesn't exist by that name
            pass 
        breadcrumbs.append({'url': obj.get_absolute_url(), 'name': str(obj)})
    elif len(path_parts) > 1:
        # If no object, but more path parts, assume the last part is the current page title
        # This is a guess and might need adjustment
        page_title = path_parts[-1].replace('-', ' ').capitalize()
        if breadcrumbs[-1]['name'].lower() != page_title.lower(): # Avoid duplicate
             breadcrumbs.append({'url': request.path, 'name': page_title})
        
    # Mark the last item as active
    if breadcrumbs:
        breadcrumbs[-1]['is_active'] = True

    return breadcrumbs
