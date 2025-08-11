# assetbox/core/forms/__init__.py
import json

from django import forms
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, HTML, Div
from django.urls import reverse
from core.search import SEARCH_INDEXES
from core.utils import get_model_viewname
import django_filters

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

class BootstrapMixin(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.PasswordInput, forms.EmailInput, forms.NumberInput)):
                field.widget.attrs.update({'class': 'form-control'})
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.update({'class': 'form-select'})
            elif isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect)):
                pass
            elif isinstance(field.widget, forms.SelectMultiple):
                 field.widget.attrs.update({'class': 'form-select'})
            elif isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.update({'class': 'form-control'})

class JournalEntryForm(forms.Form):
    comment = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))


SLACK_PAYLOAD_PRESET = json.dumps({
    "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": "AssetBox Event: {{ event }}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": "*Model:*\n{{ model }}"},
            {"type": "mrkdwn", "text": "*Object ID:*\n{{ object_id }}"}
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": "```{{ data | tojson }}```"}},
    ]
}, indent=2)

TEAMS_PAYLOAD_PRESET = json.dumps({
    "type": "message",
    "attachments": [
        {
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "body": [
                    {"type": "TextBlock", "text": "AssetBox Event: {{ event }}", "weight": "Bolder", "size": "Medium"},
                    {"type": "FactSet", "facts": [
                        {"title": "Model", "value": "{{ model }}"},
                        {"title": "Object ID", "value": "{{ object_id }}"}
                    ]},
                    {"type": "TextBlock", "text": "```{{ data | tojson }}```", "wrap": True}
                ],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.2"
            }
        }
    ]
}, indent=2)

PAYLOAD_PRESET_CHOICES = [
    ('', 'Custom'),
    ('slack', 'Slack Block Kit'),
    ('teams', 'Microsoft Teams (Adaptive Card)'),
]

PRESET_PAYLOADS = {
    'slack': SLACK_PAYLOAD_PRESET,
    'teams': TEAMS_PAYLOAD_PRESET,
}


class WebhookEndpointForm(BootstrapMixin, forms.ModelForm):
    payload_preset = forms.ChoiceField(
        choices=PAYLOAD_PRESET_CHOICES,
        required=False,
        label='Payload Preset',
        help_text='Select a preset to pre-fill the payload template above'
    )

    class Meta:
        model = None
        fields = ['name', 'url', 'http_method', 'headers', 'secret', 'enabled', 'retry_count', 'retry_backoff']
        widgets = {
            'headers': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        from core.models import WebhookEndpoint
        self._meta.model = WebhookEndpoint
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.headers:
            self.initial['headers'] = json.dumps(self.instance.headers, indent=2)

    def clean_headers(self):
        data = self.cleaned_data['headers']
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise forms.ValidationError('Headers must be valid JSON.')
        return data


class EventRuleForm(BootstrapMixin, forms.ModelForm):
    payload_preset = forms.ChoiceField(
        choices=PAYLOAD_PRESET_CHOICES,
        required=False,
        label='Payload Preset',
        help_text='Select a preset to pre-fill the action config'
    )

    class Meta:
        model = None
        fields = ['name', 'model', 'events', 'conditions', 'action_type', 'action_config', 'enabled']
        widgets = {
            'events': forms.Textarea(attrs={'rows': 3}),
            'conditions': forms.Textarea(attrs={'rows': 3}),
            'action_config': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        from core.models import EventRule
        self._meta.model = EventRule
        super().__init__(*args, **kwargs)
        if self.instance:
            if self.instance.events:
                self.initial['events'] = json.dumps(self.instance.events, indent=2)
            if self.instance.conditions:
                self.initial['conditions'] = json.dumps(self.instance.conditions, indent=2)
            if self.instance.action_config:
                self.initial['action_config'] = json.dumps(self.instance.action_config, indent=2)

    def clean_events(self):
        data = self.cleaned_data['events']
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise forms.ValidationError('Events must be valid JSON list.')
        return data

    def clean_conditions(self):
        data = self.cleaned_data['conditions']
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise forms.ValidationError('Conditions must be valid JSON.')
        return data

    def clean_action_config(self):
        data = self.cleaned_data['action_config']
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise forms.ValidationError('Action config must be valid JSON.')
        return data


class LabelTemplateForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = None
        fields = ['name', 'description', 'page_width', 'page_height', 'barcode_format', 'template_code', 'printer_settings']
        widgets = {
            'template_code': forms.Textarea(attrs={'rows': 10}),
            'printer_settings': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        from core.models import LabelTemplate
        self._meta.model = LabelTemplate
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.printer_settings:
            self.initial['printer_settings'] = json.dumps(self.instance.printer_settings, indent=2)

    def clean_printer_settings(self):
        data = self.cleaned_data['printer_settings']
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise forms.ValidationError('Printer settings must be valid JSON.')
        return data


class ConfirmationForm(BootstrapMixin, forms.Form):
    return_url = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if kwargs.get('initial') and 'return_url' in kwargs['initial']:
            self.fields['return_url'].initial = kwargs['initial']['return_url']
        elif instance and hasattr(instance, 'get_absolute_url'):
            self.fields['return_url'].initial = instance.get_absolute_url()
        elif instance:
            try:
                list_view_name = get_model_viewname(instance.__class__, 'list')
                self.fields['return_url'].initial = reverse(list_view_name)
            except Exception:
                pass


BULK_EDIT_FIELD_BLACKLIST = {
    'id', 'pk',
    'created_at', 'updated_at', 'deleted_at',
    'last_audited', 'last_audited_by',
    'signed_at', 'accepted_date', 'verification_hash',
    'slug',
}

BULK_EDIT_FIELD_TYPE_MAP = {
    'CharField': forms.CharField,
    'TextField': lambda **kw: forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), **kw),
    'IntegerField': forms.IntegerField,
    'PositiveIntegerField': forms.IntegerField,
    'BigIntegerField': forms.IntegerField,
    'PositiveBigIntegerField': forms.IntegerField,
    'SmallIntegerField': forms.IntegerField,
    'PositiveSmallIntegerField': forms.IntegerField,
    'FloatField': forms.FloatField,
    'DecimalField': forms.DecimalField,
    'BooleanField': forms.BooleanField,
    'NullBooleanField': forms.NullBooleanField,
    'DateField': forms.DateField,
    'DateTimeField': forms.DateTimeField,
    'EmailField': forms.EmailField,
    'URLField': forms.URLField,
    'ForeignKey': forms.ChoiceField,
}


class BulkEditForm(BootstrapMixin, forms.Form):
    _selected_fields = forms.MultipleChoiceField(
        widget=forms.MultipleHiddenInput(),
        required=False
    )
    add_tags = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        label="Add Tags",
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    remove_tags = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        label="Remove Tags",
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''})
    )

    def __init__(self, *args, model=None, **kwargs):
        from extras.models import Tag
        super().__init__(*args, **kwargs)
        self.fields['add_tags'].queryset = Tag.objects.all()
        self.fields['remove_tags'].queryset = Tag.objects.all()
        
        if model is None:
            return

        from django.db.models import ForeignKey, ManyToManyField
        from django.db.models.fields import related

        for field in model._meta.get_fields():
            if field.name in BULK_EDIT_FIELD_BLACKLIST:
                continue
            if not field.editable:
                continue
            if isinstance(field, (ManyToManyField, related.RelatedField)) and not isinstance(field, ForeignKey):
                continue
            if field.auto_created and field.name.endswith('_ptr'):
                continue
            if getattr(field, 'primary_key', False):
                continue

            internal_type = field.get_internal_type()
            form_field_cls = BULK_EDIT_FIELD_TYPE_MAP.get(internal_type)
            if form_field_cls is None:
                continue

            field_kwargs = {
                'label': getattr(field, 'verbose_name', field.name).title(),
                'required': False,
            }

            if isinstance(field, ForeignKey):
                related_model = field.remote_field.model
                if related_model:
                    field_kwargs['choices'] = [('', '---------')] + [
                        (obj.pk, str(obj))
                        for obj in related_model.objects.all()[:200]
                    ]

            self.fields[field.name] = form_field_cls(**field_kwargs)


class CrispyFormMixin:
    """
    Form mixin to auto-initialize FormHelper with standard settings,
    reducing crispy FormHelper boilerplate across all forms.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not hasattr(self, 'helper') or self.helper is None:
            self.helper = FormHelper(self)
            self.helper.form_method = 'post'
            self.helper.form_tag = True


class SlugModelForm(CrispyFormMixin, forms.ModelForm):
    class Media:
        js = (
            'js/slugify.js',
        )

class FilterForm(BootstrapMixin, forms.Form):
    filterset_class = None

    def __init__(self, *args, **kwargs):
        self.queryset = kwargs.pop('queryset', None)
        super(FilterForm, self).__init__(*args, **kwargs)

        if self.filterset_class is None:
            raise NotImplementedError("'filterset_class' must be defined on the FilterForm subclass.")

        filterset_data = args[0] if args else None
        self.filterset = self.filterset_class(filterset_data, queryset=self.queryset)

        for name, filter_field in self.filterset.filters.items():
            if hasattr(filter_field, 'field'):
                self.fields[name] = filter_field.field
            else:
                field_type = forms.CharField
                if isinstance(filter_field, django_filters.BooleanFilter):
                    field_type = forms.BooleanField
                elif isinstance(filter_field, django_filters.NumberFilter):
                    field_type = forms.DecimalField
                elif isinstance(filter_field, django_filters.DateFilter):
                    field_type = forms.DateField
                elif isinstance(filter_field, django_filters.DateTimeFilter):
                    field_type = forms.DateTimeField
                elif isinstance(filter_field, django_filters.MultipleChoiceFilter):
                    self.fields[name] = forms.MultipleChoiceField(
                        label=filter_field.label if filter_field.label else name.replace('_', ' ').capitalize(),
                        required=False,
                        choices=filter_field.extra.get('choices', [])
                    )
                    continue

                self.fields[name] = field_type(
                    label=filter_field.label if filter_field.label else name.replace('_', ' ').capitalize(),
                    required=False
                )

        self.helper = FormHelper()
        self.helper.form_method = 'get'
        self.helper.form_tag = False

    def search(self):
        if self.is_valid():
            return self.filterset.qs
        return self.filterset.queryset

    @property
    def applied_filters(self):
        if not self.filterset or not self.filterset.data:
            return {}

        applied = {}
        ignored_params = ['page', 'per_page', 'q']

        for name, filter_field in self.filterset.filters.items():
            if name in ignored_params:
                continue

            value = self.filterset.data.getlist(name) if hasattr(self.filterset.data, 'getlist') else self.filterset.data.get(name)

            if value:
                if isinstance(value, list):
                    value = [v for v in value if v != '']
                    if value:
                        applied[name] = value
                elif value != '':
                    applied[name] = value

        return applied


class ColorFieldFormMixin:
    """
    Mixin for forms with a 'color' hex field. Ensures '#' is prepended for the picker
    and cleaned up to raw hex when validating/saving.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if hasattr(self, 'instance') and self.instance and getattr(self.instance, 'color', None):
            self.initial['color'] = f"#{self.instance.color}"

    def clean_color(self):
        color = self.cleaned_data.get('color')
        if color and color.startswith('#'):
            cleaned_color = color[1:]
            if len(cleaned_color) == 6:
                return cleaned_color
            else:
                raise forms.ValidationError("Ensure the color hex code is 6 characters long (after removing '#').")
        elif not color:
            return ''
        if len(color) == 6:
            return color
        elif len(color) == 0:
            return ''
        else:
            raise forms.ValidationError("Ensure the color hex code is 6 characters long.")


