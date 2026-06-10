import re
from django import forms
from .models import Tag, CustomField, CustomFieldset, ConfigContext
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
        fields = ['name', 'label', 'field_type', 'choices', 'required', 'object_types']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'label': forms.TextInput(attrs={'class': 'form-control'}),
            'field_type': forms.Select(attrs={'class': 'form-select'}),
            'choices': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Value 1\nValue 2'}),
            'required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'object_types': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['name'].widget.attrs['slugify'] = 'label'

        # Only models that actually store custom field data are selectable.
        from django.contrib.contenttypes.models import ContentType
        from itambox.registry import registry
        supported = [
            ContentType.objects.get_for_model(model).pk
            for model, features in registry.model_features.items()
            if 'custom_field_data' in features and not model._meta.abstract
        ]
        self.fields['object_types'].queryset = (
            ContentType.objects.filter(pk__in=supported).order_by('app_label', 'model')
        )
        self.fields['object_types'].help_text = (
            "Models this field applies to. Fields applying to Asset Type act as "
            "hardware specifications; fields applying to Asset are per-device details."
        )

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('extras:customfield_list')

        self.helper.layout = Layout(
            'label',
            'name',
            'field_type',
            'choices',
            Div('required', css_class='mb-3 form-check'),
            'object_types',
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
        cancel_url = reverse('extras:customfieldset_list')
        
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


# =============================================================================
# Config Context
# =============================================================================

class ConfigContextForm(forms.ModelForm):
    data = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control font-monospace', 'rows': 10}),
        help_text="Enter configuration data in valid JSON format."
    )

    class Meta:
        model = ConfigContext
        fields = ['name', 'description', 'weight', 'regions', 'sites', 'locations', 'tenants', 'data']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'weight': forms.NumberInput(attrs={'class': 'form-control'}),
            'regions': forms.SelectMultiple(attrs={'class': 'form-select'}),
            'sites': forms.SelectMultiple(attrs={'class': 'form-select'}),
            'locations': forms.SelectMultiple(attrs={'class': 'form-select'}),
            'tenants': forms.SelectMultiple(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        if 'instance' in kwargs and kwargs['instance'] and kwargs['instance'].pk:
            import json
            initial = kwargs.get('initial', {})
            initial['data'] = json.dumps(kwargs['instance'].data, indent=4)
            kwargs['initial'] = initial
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('extras:configcontext_list')
        self.helper.layout = Layout(
            'name',
            'description',
            'weight',
            'regions',
            'sites',
            'locations',
            'tenants',
            'data',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

    def clean_data(self):
        data = self.cleaned_data.get('data')
        try:
            import json
            return json.loads(data)
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")


import django_filters
from django.db.models import Q

class ConfigContextFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label='Search')

    class Meta:
        model = ConfigContext
        fields = ['name', 'weight']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


class ConfigContextFilterForm(FilterForm):
    filterset_class = ConfigContextFilterSet


import django_tables2 as tables
from django_tables2.utils import A
from core.tables import ActionsColumn, BaseTable, ToggleColumn

class ConfigContextTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('extras:configcontext_edit', args=[A('pk')], verbose_name='Name')
    weight = tables.Column(verbose_name='Weight')
    description = tables.Column(verbose_name='Description')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ConfigContext
        fields = ('pk', 'name', 'weight', 'description', 'actions')
        default_columns = ('pk', 'name', 'weight', 'description', 'actions')

