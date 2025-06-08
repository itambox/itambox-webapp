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

# Remove TableConfigForm definition
# class TableConfigForm(forms.Form): ...

# Remove UserProfileForm and UserPreferencesForm definitions
# class UserProfileForm(forms.ModelForm): ...
# class UserPreferencesForm(forms.Form): ... 