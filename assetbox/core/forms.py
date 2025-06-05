# assetbox/core/forms.py
from django import forms
from django.utils.translation import gettext_lazy as _

class TableConfigForm(forms.Form):
    """
    Form for configuring user's table preferences.
    Inspired by NetBox - simplified to only handle selected columns for submission.
    """
    columns = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.SelectMultiple(
            attrs={'size': 10, 'class': 'form-select'}
        ),
        label=_('Selected Columns')
    )

    def __init__(self, table, *args, **kwargs):
        self.table = table
        user_config = kwargs.pop('user_config', {})
        print(f"[TableConfigForm __init__] Received user_config: {user_config}") # DEBUG

        super().__init__(*args, **kwargs)

        if table:
            all_possible_choices = [] # Store all valid column choices
            selected_column_names = user_config.get('columns', table.Meta.default_columns)
            print(f"[TableConfigForm __init__] Determined selected_column_names: {selected_column_names}") # DEBUG

            for name, column in table.columns.items():
                if name in ('pk', 'actions'):
                    continue
                choice = (name, str(column.verbose_name))
                all_possible_choices.append(choice)

            # Populate choices for the 'columns' field with ALL possible columns
            # This allows validation to pass for any valid column moved client-side.
            self.fields['columns'].choices = all_possible_choices

            # Set the initial value for the 'columns' field based on saved prefs/defaults
            self.fields['columns'].initial = selected_column_names
            print(f"[TableConfigForm __init__] Set columns.initial to: {self.fields['columns'].initial}") # DEBUG

            # Create choices for rendering the *available* columns widget (even though it's not a form field)
            # This is done dynamically in the template now.

    # Method to provide choices for the *available* columns widget in the template
    def get_available_columns_choices(self):
        if not self.table:
            return []
        
        selected_names = self.fields['columns'].initial or []
        print(f"[get_available] Using initial selected_names: {selected_names}") # DEBUG
        # Use choices from the field which contains all possibilities
        all_choices_dict = dict(self.fields['columns'].choices)
        
        available_choices = [
            (name, verbose_name) 
            for name, verbose_name in all_choices_dict.items() 
            if name not in selected_names
        ]
        return available_choices

    # Method to provide choices for the *selected* columns widget in the template
    def get_selected_columns_choices(self):
        if not self.table:
            return []

        selected_names = self.fields['columns'].initial or []
        print(f"[get_selected] Using initial selected_names: {selected_names}") # DEBUG
        all_choices_dict = dict(self.fields['columns'].choices)

        selected_choices = [
            (name, verbose_name)
            for name, verbose_name in all_choices_dict.items()
            if name in selected_names
        ]
        # Preserve order from selected_names if possible
        # selected_choices_dict = dict(selected_choices)
        # ordered_selected_choices = [(name, selected_choices_dict[name]) for name in selected_names if name in selected_choices_dict]
        # return ordered_selected_choices
        # Simplify: Return the filtered list directly without reordering for now
        print(f"[get_selected_columns_choices] Returning (unordered): {selected_choices}") # DEBUG
        return selected_choices

    @property
    def table_name(self):
        if not self.table:
            return None
        app_label = self.table.Meta.model._meta.app_label
        model_name = self.table.Meta.model._meta.model_name
        return f'{app_label}.{model_name}' 