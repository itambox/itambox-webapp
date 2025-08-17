import re
from django import forms
from .models import Tag, CustomField, CustomFieldset
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div, Field
from django.urls import reverse
from core.forms import FilterForm
from .filters import TagFilter

class TagForm(forms.ModelForm):
    # Explicitly define color field to allow '#' prefix initially
    color = forms.CharField(
        max_length=7, # Allow 7 chars initially (#aabbcc)
        required=False,
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'})
    )

    class Meta:
        model = Tag
        fields = ['name', 'slug', 'color', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '00ff00'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
            'color': 'Hexadecimal color code (e.g., 00ff00 for green).'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('extras:tag_list')
        self.helper.layout = Layout(
            'name',
            'slug',
            'color',
            'description',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

    def clean_color(self):
        color = self.cleaned_data.get('color')
        if color and color.startswith('#'):
            cleaned_color = color[1:]
            if len(cleaned_color) == 6:
                if not re.match(r'^[0-9a-fA-F]{6}$', cleaned_color):
                    raise forms.ValidationError("Enter a valid 6-character hex color code (without '#').")
                return cleaned_color
            else:
                raise forms.ValidationError("Ensure the color hex code is 6 characters long (after removing '#').")
        elif not color:
            return ''
        if len(color) == 6:
            if not re.match(r'^[0-9a-fA-F]{6}$', color):
                raise forms.ValidationError("Enter a valid 6-character hex color code.")
            return color
        elif len(color) == 0:
            return ''
        else:
             raise forms.ValidationError("Ensure the color hex code is 6 characters long.")

# --- Tag Filter Form --- 
class TagFilterForm(FilterForm):
    filterset_class = TagFilter


class CustomFieldForm(forms.ModelForm):
    class Meta:
        model = CustomField
        fields = ['name', 'label', 'field_type', 'choices', 'required']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'label': forms.TextInput(attrs={'class': 'form-control'}),
            'field_type': forms.Select(attrs={'class': 'form-select'}),
            'choices': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Value 1\nValue 2'}),
            'required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['name'].widget.attrs['slugify'] = 'label'
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:customfield_list')
        
        self.helper.layout = Layout(
            'label',
            'name',
            'field_type',
            'choices',
            Div('required', css_class='mb-3 form-check'),
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

class CustomFieldsetForm(forms.ModelForm):
    class Meta:
        model = CustomFieldset
        fields = ['name', 'fields']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'fields': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:customfieldset_list')
        
        self.helper.layout = Layout(
            'name',
            'fields',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class CustomFieldFilterForm(FilterForm):
    from .filters import CustomFieldFilterSet
    filterset_class = CustomFieldFilterSet


class CustomFieldsetFilterForm(FilterForm):
    from .filters import CustomFieldsetFilterSet
    filterset_class = CustomFieldsetFilterSet