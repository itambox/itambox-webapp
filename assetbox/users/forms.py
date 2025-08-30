import logging
from django import forms
from django.contrib.auth import get_user_model
# Import UserPreference from this app's models
from .models import UserPreference 
from django.utils.translation import gettext_lazy as _
from django.conf import settings # Import settings

logger = logging.getLogger(__name__)
User = get_user_model()

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class UserPreferencesForm(forms.Form):
    # Define fields explicitly
    pagination_per_page = forms.ChoiceField(
        choices=settings.PAGINATE_COUNT_CHOICES,
        label='Items Per Page',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    theme = forms.ChoiceField(
        choices=UserPreference.THEME_CHOICES, # Reference choices from model
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    def __init__(self, user, *args, **kwargs):
        """Load initial data from UserPreference."""
        super().__init__(*args, **kwargs)
        self.user = user
        try:
            # Use filter().first() to avoid DoesNotExist exception
            prefs = UserPreference.objects.filter(user=self.user).first()
            if prefs and prefs.data:
                pagination_prefs = prefs.data.get('pagination', {})
                theme_prefs = prefs.data.get('theme', {})
                
                initial_per_page = pagination_prefs.get('per_page', settings.DEFAULT_PAGINATE_COUNT)
                # Use THEME_LIGHT as the default
                initial_theme = theme_prefs.get('theme', UserPreference.THEME_LIGHT)
                
                # Ensure initial value is valid before setting
                if initial_per_page in dict(settings.PAGINATE_COUNT_CHOICES):
                    self.fields['pagination_per_page'].initial = initial_per_page
                else:
                    # Fallback if stored pref is invalid
                    self.fields['pagination_per_page'].initial = settings.DEFAULT_PAGINATE_COUNT
                
                if initial_theme in dict(UserPreference.THEME_CHOICES):
                     self.fields['theme'].initial = initial_theme
                else:
                     # Fallback if stored pref is invalid
                    self.fields['theme'].initial = UserPreference.THEME_LIGHT
                    
            else:
                # Set defaults if no preferences exist
                self.fields['pagination_per_page'].initial = settings.DEFAULT_PAGINATE_COUNT
                # Use THEME_LIGHT as the default
                self.fields['theme'].initial = UserPreference.THEME_LIGHT
                
        except Exception:
            # Fallback to defaults on any error loading preferences
            self.fields['pagination_per_page'].initial = settings.DEFAULT_PAGINATE_COUNT
             # Use THEME_LIGHT as the default
            self.fields['theme'].initial = UserPreference.THEME_LIGHT

    def save(self):
        """Save form data to UserPreference."""
        prefs, created_at = UserPreference.objects.get_or_create(user=self.user)
        
        # Ensure prefs.data is initialized as a dict if it's None or not set
        if prefs.data is None:
            prefs.data = {}
        
        # Update pagination preferences
        if 'pagination' not in prefs.data:
            prefs.data['pagination'] = {}
        prefs.data['pagination']['per_page'] = self.cleaned_data['pagination_per_page']

        # Update theme preferences
        if 'theme' not in prefs.data:
            prefs.data['theme'] = {}
        prefs.data['theme']['theme'] = self.cleaned_data['theme']
        
        prefs.save()

class TableConfigForm(forms.Form):
    """
    Form for configuring table columns.
    Adapted from NetBox pattern.
    """
    available_columns = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.SelectMultiple(
            attrs={'size': 10, 'class': 'form-select available-columns'}
        ),
        label=_('Available Columns')
    )
    columns = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.SelectMultiple(
            attrs={'size': 10, 'class': 'form-select selected-columns'}
        ),
        label=_('Selected Columns')
    )

    def __init__(self, table, *args, **kwargs):
        self.table = table
        user_config = kwargs.pop('user_config', {}) # e.g., {'columns': [...], 'ordering': [...]} or {}
        super().__init__(*args, **kwargs)

        logger.debug("TableConfigForm received user_config: %s", user_config)

        # Determine initial selected columns (priority: user > table default > all)
        default_cols = getattr(table.Meta, 'default_columns', None)
        initial_selected_names = user_config.get('columns', default_cols)
        # Treat empty list (from Reset) the same as None — fall back to defaults
        if not initial_selected_names:
             # Fallback if no user pref and no Meta.default_columns
             # Use Meta.fields or all non-excluded fields
             if hasattr(table.Meta, 'fields'):
                 initial_selected_names = list(table.Meta.fields)
             else:
                 exclude = getattr(table.Meta, 'exclude', ('pk', 'actions'))
                 initial_selected_names = [name for name in table.base_columns.keys() if name not in exclude]
        
        logger.debug("TableConfigForm initial selected names: %s", initial_selected_names)

        # Populate choices based on the table definition
        all_column_choices = {
            name: str(column.verbose_name) 
            for name, column in table.columns.items()
            if name not in getattr(table.Meta, 'exclude_from_config', ('pk', 'actions')) # Allow explicit exclusion from config
        }
        
        available_choices = []
        selected_choices = []

        # Populate selected_choices based on initial_selected_names order
        for name in initial_selected_names:
            if name in all_column_choices:
                selected_choices.append((name, all_column_choices[name]))
        
        # Populate available_choices with remaining columns, sorted by verbose name
        selected_names_set = set(initial_selected_names)
        available_choices = sorted(
            [
                (name, verbose)
                for name, verbose in all_column_choices.items()
                if name not in selected_names_set
            ],
            key=lambda item: item[1] # Sort by verbose name
        )

        # Assign choices to the form fields
        self.fields['available_columns'].choices = available_choices
        self.fields['columns'].choices = selected_choices
        
        logger.debug("TableConfigForm final available choices: %s", available_choices)
        logger.debug("TableConfigForm final selected choices: %s", selected_choices)

    @property
    def table_name(self):
        if not self.table:
            return None
        app_label = self.table.Meta.model._meta.app_label
        model_name = self.table.Meta.model._meta.model_name
        return f'{app_label}.{model_name}' 