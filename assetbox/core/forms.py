# assetbox/core/forms.py
from django import forms
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, HTML, Div
from django.urls import reverse
from .search import SEARCH_INDEXES
from .utils import get_model_viewname # Import utility if needed
import django_filters

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
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Search AssetBox', 'class': 'form-control'})
    )
    obj_type = forms.MultipleChoiceField(
        label='Object type(s)',
        choices=OBJ_TYPE_CHOICES,
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'id': 'id_obj_type_select'})
    )
    lookup_choices = (
        ('icontains', 'Partial match'),
        ('iexact', 'Exact match'),
        ('istartswith', 'Starts with'),
        ('iendswith', 'Ends with'),
        ('iregex', 'Regex'),
    )
    lookup = forms.ChoiceField(
        label='Lookup',
        choices=lookup_choices,
        required=False,
        initial='icontains',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    # --- End Lookup Field --- 

    # Remove __init__ method if not using Crispy Helper
    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     self.helper = FormHelper()
    #     ...

# Remove TableConfigForm definition
# class TableConfigForm(forms.Form): ...

# Remove UserProfileForm and UserPreferencesForm definitions
# class UserProfileForm(forms.ModelForm): ...
# class UserPreferencesForm(forms.Form): ... 

class BootstrapMixin(forms.Form):
    """
    Adds the base Bootstrap CSS classes to form elements.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.PasswordInput, forms.EmailInput, forms.NumberInput)):
                field.widget.attrs.update({'class': 'form-control'})
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.update({'class': 'form-select'})
            elif isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect)):
                # Checkboxes/radios might need form-check-input on the input itself,
                # and the label needs form-check-label. This is often handled by templates.
                pass
            elif isinstance(field.widget, forms.SelectMultiple):
                 field.widget.attrs.update({'class': 'form-select'})
            elif isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.update({'class': 'form-control'}) # Basic styling

class ConfirmationForm(BootstrapMixin, forms.Form):
    """Generic confirmation form."""
    # Add a hidden field to convey the return URL
    return_url = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, instance=None, **kwargs):
        """
        instance: The object being confirmed for deletion/action.
        """
        super().__init__(*args, **kwargs)
        self.instance = instance
        # Optionally, pre-populate return_url if provided
        if kwargs.get('initial') and 'return_url' in kwargs['initial']:
            self.fields['return_url'].initial = kwargs['initial']['return_url']
        elif instance and hasattr(instance, 'get_absolute_url'):
            # Default return URL to object detail view if possible
            self.fields['return_url'].initial = instance.get_absolute_url()
        elif instance:
            # Fallback: try to get list view URL
            try:
                list_view_name = get_model_viewname(instance.__class__, 'list')
                self.fields['return_url'].initial = reverse(list_view_name)
            except Exception:
                pass # If all else fails, return_url remains empty

class SlugModelForm(forms.ModelForm):
    """Base ModelForm for models that include a slug field."""

    class Media:
        # Define the JavaScript file needed for slug functionality
        js = (
            'js/slugify.js', # Path relative to STATIC_URL
        )

# --- Base FilterForm --- 
class FilterForm(BootstrapMixin, forms.Form):
    """Base Form for FilterSets.

    Takes a FilterSet class and adapts its fields for rendering within a standard Django form.
    """
    filterset_class = None

    def __init__(self, *args, **kwargs):
        self.queryset = kwargs.pop('queryset', None)
        super(FilterForm, self).__init__(*args, **kwargs) # Initialize Form & BootstrapMixin

        if self.filterset_class is None:
            raise NotImplementedError("'filterset_class' must be defined on the FilterForm subclass.")

        filterset_data = args[0] if args else None
        self.filterset = self.filterset_class(filterset_data, queryset=self.queryset)

        # Copy FilterSet fields to the form
        for name, filter_field in self.filterset.filters.items():
            if hasattr(filter_field, 'field'): # Handles ModelChoiceFilter, ModelMultipleChoiceFilter etc.
                self.fields[name] = filter_field.field
            else:
                # Basic type mapping for common filters
                field_type = forms.CharField # Default
                if isinstance(filter_field, django_filters.BooleanFilter):
                    field_type = forms.BooleanField
                elif isinstance(filter_field, django_filters.NumberFilter):
                    field_type = forms.DecimalField # Or IntegerField depending on need
                elif isinstance(filter_field, django_filters.DateFilter):
                    field_type = forms.DateField
                elif isinstance(filter_field, django_filters.DateTimeFilter):
                    field_type = forms.DateTimeField
                elif isinstance(filter_field, django_filters.MultipleChoiceFilter):
                    # Use the choices defined on the filter
                    self.fields[name] = forms.MultipleChoiceField(
                        label=filter_field.label if filter_field.label else name.replace('_', ' ').capitalize(),
                        required=False,
                        choices=filter_field.extra.get('choices', []) # Get choices from filter
                    )
                    continue # Skip default field creation at the end
                # Add more specific mappings if needed
                
                # Create the field instance
                self.fields[name] = field_type(
                    label=filter_field.label if filter_field.label else name.replace('_', ' ').capitalize(),
                    required=False 
                )
        
        # Add FormHelper
        self.helper = FormHelper()
        self.helper.form_method = 'get' # Filters use GET
        self.helper.form_tag = False # Template provides the <form> tag
        # No explicit layout needed, Crispy will render fields sequentially by default
        # --- End Add FormHelper --- 

    def search(self):
        """Returns the filtered queryset if the form is valid, else the original."""
        if self.is_valid():
            return self.filterset.qs
        return self.filterset.queryset # Return unfiltered queryset on invalid form

    @property
    def applied_filters(self):
        """
        Returns a dictionary of filters currently applied to the queryset,
        excluding pagination ('page', 'per_page') and quick search ('q').
        """
        if not self.filterset or not self.filterset.data:
            return {}

        applied = {}
        ignored_params = ['page', 'per_page', 'q']

        for name, filter_field in self.filterset.filters.items():
            if name in ignored_params:
                continue

            # Get list of values or single value depending on the field type
            value = self.filterset.data.getlist(name) if hasattr(self.filterset.data, 'getlist') else self.filterset.data.get(name)

            if value:
                if isinstance(value, list):
                    # Filter out empty strings from list filters (like multiple choice fields)
                    value = [v for v in value if v != '']
                    if value:
                        applied[name] = value
                elif value != '':
                    applied[name] = value

        return applied


# You can add other core forms below if needed 