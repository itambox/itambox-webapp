from django import forms
from django.contrib.auth import get_user_model
# Import UserPreference from this app's models
from .models import UserPreference 
from django.utils.translation import gettext_lazy as _

User = get_user_model()

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class UserPreferencesForm(forms.Form):
    # Define fields explicitly
    pagination_per_page = forms.IntegerField(
        label='Items Per Page',
        min_value=1,
    )
    theme = forms.ChoiceField(
        choices=UserPreference.THEME_CHOICES, # Reference choices from core model
        required=False,
        widget=forms.Select()
    )

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

        print(f"[TableConfigForm] Received user_config: {user_config}") # DEBUG

        # Determine initial selected columns (priority: user > table default > all)
        default_cols = getattr(table.Meta, 'default_columns', None)
        initial_selected_names = user_config.get('columns', default_cols)
        if initial_selected_names is None:
             # Fallback if no user pref and no Meta.default_columns
             # Use Meta.fields or all non-excluded fields
             if hasattr(table.Meta, 'fields'):
                 initial_selected_names = list(table.Meta.fields)
             else:
                 exclude = getattr(table.Meta, 'exclude', ('pk', 'actions'))
                 initial_selected_names = [name for name in table.base_columns.keys() if name not in exclude]
        
        print(f"[TableConfigForm] Initial selected names: {initial_selected_names}") # DEBUG

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
        
        print(f"[TableConfigForm] Final available choices: {available_choices}") # DEBUG
        print(f"[TableConfigForm] Final selected choices: {selected_choices}") # DEBUG

    @property
    def table_name(self):
        if not self.table:
            return None
        app_label = self.table.Meta.model._meta.app_label
        model_name = self.table.Meta.model._meta.model_name
        return f'{app_label}.{model_name}' 