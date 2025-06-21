import logging
import django_tables2 as tables
from django_tables2.utils import A # Ensure A is imported
from .models import ObjectChange
from django.utils.html import format_html, escape
from django.urls import reverse, NoReverseMatch
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from .utils import get_model_viewname # Import the utility function
from django.contrib.contenttypes.models import ContentType # Ensure ContentType is imported

logger = logging.getLogger(__name__)

# =============================================================================
# Custom Columns
# =============================================================================

class BooleanColumn(tables.Column):
    """
    Custom column for rendering boolean values as icons (check/cross).
    """
    def render(self, value, record, bound_column):
        if value is True:
            return mark_safe('<span class="text-success"><svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-circle-check" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0" /><path d="M9 12l2 2l4 -4" /></svg></span>')
        elif value is False:
            return mark_safe('<span class="text-danger"><svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-circle-x" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0" /><path d="M10 10l4 4m0 -4l-4 4" /></svg></span>')
        else:
            return "—" # Render dash for None or other values

# Base Table for common settings
class BaseTable(tables.Table):
    class Meta:
        model = None # Should be overridden by subclasses
        # Use tuple for attributes
        attrs = {
            'class': 'table table-hover table-vcenter card-table', 
            'thead': {
                'class': 'text-nowrap'
            }
        }
        # Define default columns to exclude from configuration modal
        exclude_from_config = ('pk', 'actions')
    
    # Ensure __init__ is outside Meta
    def __init__(self, *args, **kwargs):
        # Extract request if present in kwargs to load user preferences
        request = kwargs.get('request', None)
        
        # Call super() FIRST to initialize self.columns
        super().__init__(*args, **kwargs)

        # Get the full set of defined column names *before* hiding
        base_column_names = set(self.columns.names())

        model = getattr(self.Meta, 'model', None)
        user_columns = None

        # Get user preferences if available
        if model is not None and request and request.user.is_authenticated:
            try:
                from django.apps import apps
                UserPreference = apps.get_model('users', 'UserPreference')
                prefs = UserPreference.objects.filter(user=request.user).first()
                if prefs and prefs.data:
                    app_label = model._meta.app_label
                    table_class_name = self.__class__.__name__
                    user_config = prefs.data.get('tables', {}).get(app_label, {}).get(table_class_name, {})
                    user_columns = user_config.get('columns')
                    logger.debug("Found user columns for %s.%s: %s", app_label, table_class_name, user_columns)
            except Exception as e:
                logger.error("Error getting user table prefs: %s", e)
                pass

        # Determine the effective list of columns to show
        if user_columns is not None:
            columns_to_show = user_columns
        else:
            columns_to_show = getattr(self.Meta, 'default_columns', ())

        # Define columns that should *always* be visible
        exempt_columns = ('pk', 'actions')

        # Hide columns NOT in columns_to_show and NOT exempt
        for name, column in self.columns.items():
            if name not in columns_to_show and name not in exempt_columns:
                self.columns.hide(name)
            elif name in exempt_columns and hasattr(column, 'visible') and not column.visible:
                 self.columns.show(name)

        # Rearrange sequence
        final_sequence_list = [
            *[c for c in columns_to_show if c in base_column_names],
            *[c for c in base_column_names if c not in columns_to_show]
        ]

        if 'pk' in final_sequence_list:
            final_sequence_list.remove('pk')
            final_sequence_list.insert(0, 'pk')

        if 'actions' in final_sequence_list:
            final_sequence_list.remove('actions')
            final_sequence_list.append('actions')

        self.sequence = tuple(final_sequence_list)

class ObjectChangeTable(tables.Table):
    """
    Table for displaying ObjectChange entries.
    """
    time = tables.DateTimeColumn(linkify=True, format='Y-m-d H:i:s')
    user_name = tables.Column(verbose_name='User')
    action = tables.Column(verbose_name='Action')
    changed_object_type = tables.Column(linkify=False, verbose_name='Type')
    object_repr = tables.Column(linkify=False, verbose_name='Object')
    request_id = tables.Column(linkify=False, verbose_name='Request ID')

    # Custom column to linkify the changed object if possible
    changed_object = tables.Column(
        linkify=lambda record: record.get_changed_object_url(),
        verbose_name='Changed Object',
        accessor='object_repr' # Display the object_repr in the column
    )

    class Meta:
        model = ObjectChange
        fields = (
            'time', 'user_name', 'action', 'changed_object_type', 'changed_object',
            'request_id',
        )
        attrs = {
            'class': 'table table-hover object-list'
        }
        # Define default sequence if needed, otherwise follows fields
        # sequence = ('time', 'user_name', 'action', 'changed_object_type', 'changed_object', 'request_id') 

class ActionsColumn(tables.Column):
    """
    Renders a dropdown menu with edit and delete links for an object.
    Derives URL names based on the model.
    """
    attrs = {'td': {'class': 'text-end text-nowrap noprint'}} # Mimic NetBox attrs
    empty_values = ()
    verbose_name = '' # No header text
    orderable = False

    def render(self, record, table, **kwargs):
        # Ensure we have a record with a pk
        if not record or not getattr(record, 'pk', None):
            return self.default

        # Get URLs dynamically
        model = type(record)
        try:
            url_edit = reverse(get_model_viewname(model, 'update'), kwargs={'pk': record.pk})
        except:
            url_edit = None # Handle case where update view doesn't exist

        try:
            url_delete = reverse(get_model_viewname(model, 'delete'), kwargs={'pk': record.pk})
        except:
            url_delete = None # Handle case where delete view doesn't exist

        # Add other URLs like changelog later if needed

        # Use Tabler icons SVG markup
        icon_edit = '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-pencil" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 20h4l10.5 -10.5a1.5 1.5 0 0 0 -4 -4l-10.5 10.5v4" /><path d="M13.5 6.5l4 4" /></svg>'
        icon_delete = '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-trash" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M4 7l16 0" /><path d="M10 11l0 6" /><path d="M14 11l0 6" /><path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12" /><path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3" /></svg>'

        # Build the split button dropdown HTML
        html_parts = [
            f'''<div class="btn-group dropdown">'''
        ]

        # Edit button (if URL exists)
        if url_edit:
            html_parts.append(f'''<a href="{url_edit}" class="btn btn-sm btn-primary" title="{_('Edit')}">{icon_edit}</a>''')

        # Only show toggle and menu if there are other actions (delete for now)
        if url_delete: # Add conditions for other actions here later
            # Use primary color for toggle if edit exists, else secondary
            toggle_color = 'primary' if url_edit else 'secondary'
            html_parts.append(f'''<button class="btn btn-sm btn-{toggle_color} dropdown-toggle dropdown-toggle-split" type="button" data-bs-toggle="dropdown" aria-expanded="false">
                                   <span class="visually-hidden">{_('Toggle Dropdown')}</span>
                               </button>
                               <ul class="dropdown-menu dropdown-menu-end" data-bs-strategy="fixed">''')
            if url_delete:
                html_parts.append(f'''<li><a class="dropdown-item text-danger" href="{url_delete}">{icon_delete} {_("Delete")}</a></li>''')
            # Add other actions here
            html_parts.append('''   </ul>''')

        html_parts.append('''</div>''')

        # Don't render empty div if no actions are available
        if not url_edit and not url_delete:
            return self.default

        return mark_safe("\n".join(html_parts))

# --- Search Results Table (Aligned with NetBox approach) ---
class SearchResultTable(tables.Table):
    """
    Table for displaying heterogeneous search results (aligned with NetBox).
    The data passed to this table should be a list of objects/dicts,
    each having an '_object_type_id' and an 'object' attribute holding the actual model instance.
    """
    # Use accessor for the ContentType ID stored on the wrapper object
    object_type = tables.Column(
        accessor='_object_type_id', 
        verbose_name='Type',
        orderable=False
    )
    # Use 'object' as column name, accessing the model instance on the wrapper
    object = tables.Column(
        accessor='object', # Access the 'object' attribute of the wrapper
        linkify=True,     # Restore linkify
        verbose_name='Result',
        orderable=False # Ordering by str representation might be unreliable
    )

    class Meta:
        # No specific model - it handles multiple types
        attrs = {
            'class': 'table table-hover object-list'
        }
        # Use the new field names
        fields = ('object_type', 'object', ) 
        # sequence = (...) # Define order if needed

    # Simplified render_object_type, expects ContentType ID as value
    def render_object_type(self, value):
        try:
            ct = ContentType.objects.get_for_id(value)
            # Format similar to NetBox
            return ct.name.capitalize()
        except ContentType.DoesNotExist:
            return "Unknown Type"

    # Optional: Implement render_parent if needed
    # def render_parent(self, record):
    #     if hasattr(record, 'site'): # Example for Location
    #         return record.site
    #     # Add checks for other parent relationships
    #     return "—" 