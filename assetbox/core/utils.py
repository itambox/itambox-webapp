from .constants import DEFAULT_PAGINATE_COUNT, PAGINATE_COUNT_CHOICES
import datetime # Import datetime
from decimal import Decimal # Import Decimal
import logging
from django.conf import settings
from django.core.paginator import Paginator
from django.contrib.contenttypes.models import ContentType # Import ContentType
from django.utils.module_loading import import_string # Import import_string
from django.shortcuts import reverse
from users.models import UserPreference # Import UserPreference
from django.db.models import Model
from django.forms.models import model_to_dict

logger = logging.getLogger(__name__)

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
        logger.debug("Invalid per_page query parameter: '%s'", request.GET.get('per_page'))

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
                         logger.debug("Invalid user preference pagination value: '%s'", user_pref_val)
        except Exception as e:
            logger.debug("Error reading user pagination preferences: %s", e)

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
from .middleware import get_current_request_id, get_current_user

def serialize_object(obj: Model, extra_fields=None, exclude_fields=None) -> dict:
    """
    Serialize a model instance into a dictionary suitable for change logging.
    Excludes certain fields like primary key and AutoFields by default.
    Includes fields specified in `extra_fields`.
    Excludes fields specified in `exclude_fields`.
    Handles M2M fields by serializing their PKs.
    Converts date/datetime/Decimal to JSON-serializable formats.
    """
    if not obj:
        return None
    
    if extra_fields is None:
        extra_fields = set()
    if exclude_fields is None:
        exclude_fields = set()
    
    data = {}
    m2m_fields = {f.name for f in obj._meta.many_to_many}

    for field in obj._meta.get_fields():
        field_name = field.name
        
        # Explicitly exclude fields listed in exclude_fields (e.g. updated_at)
        if field_name in exclude_fields:
            continue

        # Exclude auto-created fields, relations handled separately, and explicitly excluded fields
        # Keep concrete fields unless they are the PK
        if not field.concrete or field.name == obj._meta.pk.name:
             if field.name not in extra_fields:
                  continue
        
        try:
            field_value = getattr(obj, field_name)
        except AttributeError:
            # This might happen for reverse relations if not excluded properly
            continue 

        if field_name in m2m_fields:
            # Serialize M2M as a list of related object PKs
            if hasattr(field_value, 'all'): # Check if it's a related manager
                 data[field_name] = sorted(list(field_value.values_list('pk', flat=True)))
            else:
                data[field_name] = []
        elif field.is_relation: # OneToOneField, ForeignKey
            # Serialize FK/O2O as related object PK
            if field_value is not None:
                data[field_name] = field_value.pk
            else:
                data[field_name] = None
        else: # Handle plain fields
            # Convert non-JSON serializable types
            if isinstance(field_value, (datetime.date, datetime.datetime)):
                data[field_name] = field_value.isoformat()
            elif isinstance(field_value, Decimal):
                 data[field_name] = str(field_value)
            # Assume other basic types are directly serializable
            else:
                data[field_name] = field_value # Store directly

                 
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

    logger.debug("User from middleware: %s", user)
    logger.debug("Request ID from middleware: %s", request_id)

    # Ensure request_id is present (should always be set by middleware in a request)
    if not request_id:
        # Handle cases outside a request (e.g., management command, shell)
        # Or decide to raise an error if logging must happen within a request.
        # For now, let's skip logging if no request_id
        logger.debug("Skipping changelog for %s (%s) - no request_id found.", instance, action)
        return

    try:
        logger.debug("Attempting ObjectChange.objects.create for %s", instance)
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
        logger.debug("ObjectChange created with PK: %s", oc.pk)
        logger.info("Changelog: Logged %s for %s (User: %s, Request: %s)", action, instance, user, request_id)
    except Exception as e:
        # Handle potential errors during logging (e.g., database error)
        logger.error("Error logging change for %s: %s", instance, e, exc_info=True)

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
        logger.warning("Could not find %s in %s.tables", table_class_name, app_label)
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
            logger.debug("List view URL not found for %s, skipping list breadcrumb", list_view_name)
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
