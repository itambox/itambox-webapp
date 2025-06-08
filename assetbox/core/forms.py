# assetbox/core/forms.py
from django import forms
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, HTML, Div
from django.urls import reverse
from .search import SEARCH_INDEXES

# Define choices for the obj_type field dynamically
# Format: ("app_label.model_name", "App Label | Model Name")
OBJ_TYPE_CHOICES = [
    (
        f"{model._meta.app_label}.{model._meta.model_name}",
        f"{model._meta.app_label.capitalize()} | {model._meta.verbose_name.capitalize()}"
    )
    for model in sorted(SEARCH_INDEXES.keys(), key=lambda m: (m._meta.app_label, m._meta.verbose_name))
]

class SearchForm(forms.Form):
    q = forms.CharField(
        label='',
        widget=forms.TextInput(attrs={'placeholder': 'Search AssetBox', 'class': 'form-control'})
    )
    obj_type = forms.ChoiceField(
        label='Object Type',
        choices=[('', 'All Types')] + OBJ_TYPE_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'get'
        self.helper.form_action = reverse('search')
        self.helper.layout = Layout(
            Div(
                Field('q', css_class=''),
                HTML('<button type="submit" class="btn btn-primary"><i class="ti ti-search"></i> Search</button>'),
                css_class='input-group mb-3'
            ),
            Field('obj_type', css_class='')
        )
        self.helper.form_tag = False
        self.helper.disable_csrf = True

class TableConfigForm(forms.Form):
    """
    Form for configuring table columns.
    Adapted from NetBox pattern.
    """
    # Hidden field to pass table name to JS/API
    # table_name = forms.CharField(widget=forms.HiddenInput())

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
        user_config = kwargs.pop('user_config', {})

        super().__init__(*args, **kwargs)

        # Determine initial selected columns (from user prefs or table defaults)
        initial_selected_names = user_config.get('columns', getattr(table.Meta, 'default_columns', []))

        # Populate choices based on the table definition
        all_choices_dict = {}
        available_choices = []
        selected_choices = []

        for name, column in table.columns.items():
            # Exclude non-configurable columns (like pk or specific action columns)
            if name in getattr(table.Meta, 'exclude_columns', ('pk', 'actions')):
                continue
            choice = (name, str(column.verbose_name))
            all_choices_dict[name] = choice
            if name not in initial_selected_names:
                available_choices.append(choice)

        # Build ordered selected choices based on initial_selected_names
        for name in initial_selected_names:
            if name in all_choices_dict:
                selected_choices.append(all_choices_dict[name])

        # Assign choices to the form fields
        self.fields['available_columns'].choices = available_choices
        self.fields['columns'].choices = selected_choices

        # No initial values needed as choices dictate the lists

    @property
    def table_name(self):
        # Helper to get the app_label.model_name string for API/config key
        if not self.table:
            return None
        app_label = self.table.Meta.model._meta.app_label
        model_name = self.table.Meta.model._meta.model_name
        return f'{app_label}.{model_name}'

# Remove UserProfileForm and UserPreferencesForm definitions
# class UserProfileForm(forms.ModelForm): ...
# class UserPreferencesForm(forms.Form): ... 