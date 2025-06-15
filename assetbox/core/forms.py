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

class SlugModelForm(forms.ModelForm):
    """Base ModelForm for models that include a slug field."""

    class Media:
        # Define the JavaScript file needed for slug functionality
        js = (
            'js/slugify.js', # Path relative to STATIC_URL
        )

# You can add other core forms below if needed 