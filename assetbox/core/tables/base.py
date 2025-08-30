# assetbox/core/tables/base.py
import logging
import django_tables2 as tables

logger = logging.getLogger(__name__)

# SESSION_KEY_PREFIX = 'table_config_' # No longer needed

class BaseTable(tables.Table):
    """
    Base table for models, providing dynamic column selection based on user preferences.
    """
    class Meta:
        # Default attributes, can be overridden by subclasses
        attrs = {"class": "table table-vcenter card-table table-hover"}
        template_name = "global_includes\htmx_table.html"
        default_columns = () # Subclasses should define this

    def __init__(self, *args, request=None, **kwargs):
        # Call super() FIRST to initialize self.columns
        super().__init__(*args, **kwargs)

        # Get the full set of defined column names *before* hiding
        base_column_names = set(self.columns.names())
        logger.debug("Initial base_column_names: %s", base_column_names)

        # --- POST-SUPER LOGIC (NetBox Approach) ---
        model = self.Meta.model
        user_columns = None # User preference list

        # Get user preferences if available
        if request and request.user.is_authenticated:
            try:
                from django.apps import apps
                UserPreference = apps.get_model('users', 'UserPreference')
                prefs = UserPreference.objects.filter(user=request.user).first()
                if prefs:
                    app_label = model._meta.app_label
                    table_class_name = self.__class__.__name__
                    user_config = prefs.data.get('tables', {}).get(app_label, {}).get(table_class_name, {})
                    user_columns = user_config.get('columns')
                    logger.debug("Found user columns: %s", user_columns)
                else:
                    logger.debug("No UserPreference object found")
            except Exception as e:
                logger.error("Error getting user column preferences: %s", e)

        # Determine the effective list of columns to show
        # Treat both None and empty list as "no preference" so that Reset
        # (which stores columns: []) and fresh users both fall back to defaults
        if user_columns:
            columns_to_show = user_columns
            logger.debug("Using user preference columns: %s", columns_to_show)
        else:
            # Fallback to defaults defined in Meta
            columns_to_show = self.Meta.default_columns
            logger.debug("No preferences found, using default columns: %s", columns_to_show)

        # Define columns that should *always* be visible if defined, regardless of user prefs
        # In our case, 'pk' and 'actions'
        exempt_columns = ('pk', 'actions')

        # Hide columns NOT in columns_to_show and NOT exempt
        for name, column in self.columns.items():
            if name not in columns_to_show and name not in exempt_columns:
                self.columns.hide(name)
                logger.debug("Hiding column: %s", name)
            # Ensure exempt columns are visible if they exist (they might be hidden by default)
            elif name in exempt_columns and hasattr(column, 'visible') and not column.visible:
                 self.columns.show(name)
                 logger.debug("Ensuring exempt column is visible: %s", name)

        # Rearrange the sequence to list selected columns first, followed by all remaining columns
        # Trusting that hide() handles visibility and sequence primarily handles order.
        final_sequence_list = [
            # Part 1: User's selected/shown columns that actually exist
            # Use base_column_names for safety
            *[c for c in columns_to_show if c in base_column_names],
            # Part 2: ALL OTHER columns defined in the table (even hidden ones)
            # Use base_column_names for safety
            *[c for c in base_column_names if c not in columns_to_show]
        ]

        # Move pk to start if present
        if 'pk' in final_sequence_list:
            final_sequence_list.remove('pk')
            final_sequence_list.insert(0, 'pk')

        # Move actions to end if present
        if 'actions' in final_sequence_list:
            final_sequence_list.remove('actions')
            final_sequence_list.append('actions')

        self.sequence = tuple(final_sequence_list)
        logger.debug("Final sequence set to (NetBox Logic): %s", self.sequence)
        # --- End NetBox Sequence --- 

        # No need to set exclude explicitly if hide() works 