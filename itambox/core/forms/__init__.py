# itambox/core/forms/__init__.py
import json

from django import forms
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, HTML, Div
from django.urls import reverse
from core.search import SEARCH_INDEXES
from itambox.utils import get_model_viewname
import django_filters
from itambox.middleware import get_current_user
from extras.models import WebhookEndpoint, EventRule, Event, LabelTemplate, ReportTemplate, ScheduledReport, AlertRule, NotificationChannel


def logged_content_types():
    """ContentTypes whose model actually emits Events (ChangeLoggingMixin, not skipped).

    An EventRule pointed at any other model would never fire — constraining the dropdown
    to these keeps users out of that silent dead-end.
    """
    from core.models import ChangeLoggingMixin
    from core.signals import _SIGNAL_SKIP_MODELS

    ids = []
    for ct in ContentType.objects.all():
        model = ct.model_class()
        if model is None:
            continue
        if issubclass(model, ChangeLoggingMixin) and model.__name__ not in _SIGNAL_SKIP_MODELS:
            ids.append(ct.id)
    return ContentType.objects.filter(id__in=ids).order_by('app_label', 'model')

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
        widget=forms.TextInput(attrs={'placeholder': 'Search ITAMbox', 'class': 'form-control'})
    )
    obj_type = forms.MultipleChoiceField(
        label='Object type(s)',
        choices=OBJ_TYPE_CHOICES,
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'id': 'id_obj_type_select', 'data-tom-select': ''})
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

class JournalEntryForm(forms.Form):
    comment = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))


SLACK_PAYLOAD_PRESET = json.dumps({
    "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": "ITAMbox Event: {{ event }}"}},
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
                    {"type": "TextBlock", "text": "ITAMbox Event: {{ event }}", "weight": "Bolder", "size": "Medium"},
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


class WebhookEndpointForm(forms.ModelForm):
    payload_preset = forms.ChoiceField(
        choices=PAYLOAD_PRESET_CHOICES,
        required=False,
        label='Payload Preset',
        help_text='Select a preset to pre-fill the payload template above'
    )

    class Meta:
        model = WebhookEndpoint
        fields = ['name', 'url', 'http_method', 'headers', 'secret', 'enabled', 'retry_count', 'retry_backoff']
        widgets = {
            'headers': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
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


class EventRuleForm(forms.ModelForm):
    events = forms.MultipleChoiceField(
        choices=Event.ACTION_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label='Trigger Events',
        help_text='Fire this rule when any of the selected change types occur on the target model.',
    )
    payload_preset = forms.ChoiceField(
        choices=PAYLOAD_PRESET_CHOICES,
        required=False,
        label='Payload Preset',
        help_text='Select a preset to pre-fill the action config'
    )

    class Meta:
        model = EventRule
        fields = ['name', 'model', 'events', 'action_type', 'webhook', 'conditions', 'action_config', 'enabled']
        widgets = {
            'conditions': forms.Textarea(attrs={'rows': 3}),
            'action_config': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only models that emit Events are selectable — others would never trigger the rule.
        self.fields['model'].queryset = logged_content_types()
        self.fields['model'].label = 'Target Model'
        self.fields['webhook'].queryset = WebhookEndpoint.objects.filter(enabled=True)
        self.fields['webhook'].label = 'Webhook Endpoint'
        self.fields['webhook'].help_text = (
            'Required for Webhook rules. Manage endpoints under Webhook Endpoints. '
            'Leave blank only if you supply a "url" in Action Configuration below.'
        )
        self.fields['conditions'].required = False
        self.fields['action_config'].required = False
        if self.instance and self.instance.pk:
            if self.instance.events:
                self.initial['events'] = self.instance.events
            if self.instance.conditions:
                self.initial['conditions'] = json.dumps(self.instance.conditions, indent=2)
            if self.instance.action_config:
                self.initial['action_config'] = json.dumps(self.instance.action_config, indent=2)

    def clean_conditions(self):
        data = self.cleaned_data['conditions']
        if isinstance(data, str):
            if not data.strip():
                return {}
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise forms.ValidationError('Conditions must be valid JSON.')
        return data or {}

    def clean_action_config(self):
        data = self.cleaned_data['action_config']
        if isinstance(data, str):
            if not data.strip():
                return {}
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise forms.ValidationError('Action config must be valid JSON.')
        return data or {}

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get('action_type')
        if action_type == EventRule.ACTION_WEBHOOK:
            config = cleaned.get('action_config') or {}
            if not cleaned.get('webhook') and not config.get('url'):
                self.add_error(
                    'webhook',
                    'Select a Webhook Endpoint (or provide a "url" in Action Configuration) for a Webhook rule.',
                )
        return cleaned


class LabelTemplateForm(forms.ModelForm):
    class Meta:
        model = LabelTemplate
        fields = ['name', 'description', 'page_width', 'page_height', 'barcode_format', 'template_code']
        widgets = {
            'template_code': forms.Textarea(attrs={'rows': 10}),
        }


class ConfirmationForm(forms.Form):
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


class BulkEditForm(forms.Form):
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

        choices = [
            ('add_tags', 'add_tags'),
            ('remove_tags', 'remove_tags'),
        ]
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
            if getattr(field, 'choices', None):
                form_field_cls = forms.ChoiceField
            else:
                form_field_cls = BULK_EDIT_FIELD_TYPE_MAP.get(internal_type)
            if form_field_cls is None:
                continue

            field_kwargs = {
                'label': getattr(field, 'verbose_name', field.name).title(),
                'required': False,
            }

            if getattr(field, 'choices', None):
                field_kwargs['choices'] = [('', '---------')] + list(field.choices)
            elif isinstance(field, ForeignKey):
                related_model = field.remote_field.model
                if related_model:
                    field_kwargs['choices'] = [('', '---------')] + [
                        (obj.pk, str(obj))
                        for obj in related_model.objects.all()[:200]
                    ]

            self.fields[field.name] = form_field_cls(**field_kwargs)
            choices.append((field.name, field.name))

        self.fields['_selected_fields'].choices = choices

        # Auto-apply Bootstrap classes and TomSelect attribute to all fields in BulkEditForm
        for field_name, field in self.fields.items():
            if field_name == '_selected_fields':
                continue

            # Apply dynamic classes
            existing_classes = field.widget.attrs.get('class', '').split()
            if isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                target_class = 'form-check-input'
            elif isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                target_class = 'form-select'
            else:
                target_class = 'form-control'

            if target_class not in existing_classes:
                existing_classes.append(target_class)
                field.widget.attrs['class'] = ' '.join(existing_classes)

            # Auto-apply TomSelect attribute to all select fields (excluding CheckboxSelectMultiple/RadioSelect/listboxes)
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) and not isinstance(field.widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                if 'size' in field.widget.attrs:
                    continue
                widget_classes = field.widget.attrs.get('class', '')
                if 'available-columns' not in widget_classes and 'selected-columns' not in widget_classes:
                    if 'data-tom-select' not in field.widget.attrs:
                        field.widget.attrs['data-tom-select'] = ''


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

class FilterForm(forms.Form):
    filterset_class = None

    def __init__(self, *args, **kwargs):
        self.queryset = kwargs.pop('queryset', None)
        super(FilterForm, self).__init__(*args, **kwargs)

        if self.filterset_class is None:
            raise NotImplementedError("'filterset_class' must be defined on the FilterForm subclass.")

        filterset_data = args[0] if args else kwargs.get('data', None)
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

        ajax_fields = getattr(self, 'ajax_fields', None)
        if ajax_fields:
            self.setup_ajax_fields(ajax_fields, filterset_data)

        # Auto-apply TomSelect attribute to all select fields (excluding CheckboxSelectMultiple/RadioSelect/listboxes)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) and not isinstance(field.widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                if 'size' in field.widget.attrs:
                    continue
                widget_classes = field.widget.attrs.get('class', '')
                if 'available-columns' not in widget_classes and 'selected-columns' not in widget_classes:
                    if 'data-tom-select' not in field.widget.attrs:
                        field.widget.attrs['data-tom-select'] = ''

    def setup_ajax_fields(self, ajax_fields, filterset_data):
        for field_name, config in ajax_fields.items():
            if field_name not in self.fields:
                continue

            field = self.fields[field_name]
            url = reverse(config['url_name'])

            field.widget.attrs.update({
                'data-tom-select': '',
                'data-tom-select-url': url,
                'data-tom-select-value-field': config.get('value_field', 'id'),
                'data-tom-select-label-field': config.get('label_field', 'name'),
            })

            if hasattr(field, 'queryset'):
                selected_vals = []
                if filterset_data:
                    if hasattr(filterset_data, 'getlist'):
                        selected_vals = filterset_data.getlist(field_name)
                    else:
                        val = filterset_data.get(field_name)
                        if val:
                            if isinstance(val, list):
                                selected_vals = val
                            else:
                                selected_vals = [val]
                elif self.initial and field_name in self.initial:
                    val = self.initial[field_name]
                    if isinstance(val, list):
                        selected_vals = val
                    else:
                        selected_vals = [val]

                # Convert model instances to PK/to_field_name values if necessary, and filter empty values
                to_field = getattr(field, 'to_field_name', None) or 'pk'
                cleaned_vals = []
                for val in selected_vals:
                    if val is None or val == '':
                        continue
                    if hasattr(val, to_field):
                        cleaned_vals.append(getattr(val, to_field))
                    elif hasattr(val, 'pk'):
                        cleaned_vals.append(val.pk)
                    else:
                        cleaned_vals.append(val)

                if cleaned_vals:
                    filter_kwargs = {f"{to_field}__in": cleaned_vals}
                    field.queryset = field.queryset.filter(**filter_kwargs)
                else:
                    field.queryset = field.queryset.none()

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


class ReportTemplateForm(forms.ModelForm):
    COLUMN_CHOICES = [
        # Asset Inventory Summary Columns
        ('asset_tag', _('Asset Tag')),
        ('name', _('Asset Name')),
        ('manufacturer', _('Manufacturer')),
        ('model', _('Model')),
        ('serial_number', _('Serial Number')),
        ('status', _('Status Label')),
        ('location', _('Location')),
        ('assigned_to', _('Asset Holder')),
        ('purchase_cost', _('Purchase Cost')),
        ('purchase_date', _('Purchase Date')),
        ('warranty_months', _('Warranty (Months)')),
        # License Utilization Columns
        ('license_name', _('License Name')),
        ('software', _('Software')),
        ('seats', _('Total Seats')),
        ('assigned_seats', _('Assigned Seats')),
        ('available_seats', _('Available Seats')),
        ('utilization_rate', _('Utilization Rate')),
        # Subscription Renewals Columns
        ('subscription_name', _('Subscription Name')),
        ('provider', _('Provider')),
        ('billing_cycle', _('Billing Cycle')),
        ('cost', _('Cost')),
        ('end_date', _('End Date')),
        # Asset Maintenance Columns
        ('maintenance_title', _('Maintenance Title')),
        ('maintenance_asset', _('Asset')),
        ('maintenance_type', _('Type')),
        ('maintenance_status', _('Status')),
        ('maintenance_cost', _('Cost')),
        ('maintenance_start_date', _('Start Date')),
        ('maintenance_completion_date', _('Completion Date')),
        ('maintenance_downtime', _('Downtime (Days)')),
    ]

    included_columns = forms.MultipleChoiceField(
        choices=COLUMN_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        required=False,
        label=_("Included Columns"),
        help_text=_("Select the columns to include in your visual report grid. Only columns matching your report type will render.")
    )

    class Meta:
        model = ReportTemplate
        fields = [
            'name', 'description', 'report_type', 'included_columns',
            'include_summary_cards', 'include_distribution_chart',
            'group_by_field', 'style_preset', 'advanced_mode', 'template_content', 'tenant', 'filter_tenants'
        ]
        widgets = {
            'template_content': forms.Textarea(attrs={'rows': 15, 'style': 'font-family: monospace;'}),
            'tenant': forms.Select(attrs={'class': 'form-select'}),
            'filter_tenants': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''})
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = get_current_user()
        if user and not (user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)):
            if 'tenant' in self.fields:
                self.fields.pop('tenant')
            if 'filter_tenants' in self.fields:
                self.fields.pop('filter_tenants')
        else:
            if 'tenant' in self.fields:
                self.fields['tenant'].required = False
                self.fields['tenant'].label = _("Scope / Tenant Filter")
                self.fields['tenant'].empty_label = _("--------- All Tenants ---------")
                self.fields['tenant'].help_text = _("Select a specific tenant to restrict this report's compiled data strictly to that tenant. Choose 'All Tenants' (blank) to aggregate data globally across all tenants.")
            if 'filter_tenants' in self.fields:
                self.fields['filter_tenants'].label = _("Filter Tenants (Scoping Constellation)")
                self.fields['filter_tenants'].help_text = _("Select one or more specific tenants to filter this report's compiled data. If none are selected, aggregates data globally across all tenants.")

    def save(self, commit=True):
        instance = super().save(commit=False)
        user = get_current_user()
        if user and not (user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)):
            from core.managers import get_current_tenant
            profile = user.asset_holder_profiles.filter(tenant=get_current_tenant()).first() if get_current_tenant() else user.asset_holder_profiles.first()
            if profile and profile.tenant:
                instance.tenant = profile.tenant
        if commit:
            instance.save()
            self.save_m2m()
        return instance



class ScheduledReportForm(forms.ModelForm):
    class Meta:
        model = ScheduledReport
        fields = [
            'name', 'report', 'recipients', 'frequency', 'cron_expression', 'start_time',
            'format', 'channels', 'save_to_archive', 'is_active', 'tenant', 'filter_tenants'
        ]
        widgets = {
            'recipients': forms.Textarea(attrs={'rows': 2, 'placeholder': 'recipient1@example.com, recipient2@example.com'}),
            'tenant': forms.Select(attrs={'class': 'form-select'}),
            'report': forms.Select(attrs={'class': 'form-select'}),
            'filter_tenants': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'channels': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'cron_expression': forms.TextInput(attrs={'placeholder': 'e.g. 0 8 * * 1-5', 'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = get_current_user()
        if user and not (user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)):
            if 'tenant' in self.fields:
                self.fields.pop('tenant')
            if 'filter_tenants' in self.fields:
                self.fields.pop('filter_tenants')
            # Filter reports and channels choices dynamically
            from core.managers import get_current_tenant
            profile = user.asset_holder_profiles.filter(tenant=get_current_tenant()).first() if get_current_tenant() else user.asset_holder_profiles.first()
            if profile and profile.tenant:
                from extras.models import ReportTemplate
                from extras.models import NotificationChannel
                from django.db.models import Q
                self.fields['report'].queryset = ReportTemplate.objects.filter(
                    Q(tenant=profile.tenant) | Q(tenant__isnull=True)
                )
                self.fields['channels'].queryset = NotificationChannel.objects.filter(
                    Q(tenant=profile.tenant) | Q(tenant__isnull=True)
                )
        else:
            if 'tenant' in self.fields:
                self.fields['tenant'].required = False
                self.fields['tenant'].label = _("Scope / Tenant Filter")
                self.fields['tenant'].empty_label = _("--------- All Tenants ---------")
                self.fields['tenant'].help_text = _("Select a specific tenant to restrict this scheduled report's compiled data strictly to that tenant. Choose 'All Tenants' (blank) to aggregate data globally across all tenants.")
            if 'filter_tenants' in self.fields:
                self.fields['filter_tenants'].label = _("Filter Tenants (Scoping Constellation)")
                self.fields['filter_tenants'].help_text = _("Select one or more specific tenants to filter this scheduled report's compiled data. If none are selected, aggregates data globally across all tenants.")

    def save(self, commit=True):
        instance = super().save(commit=False)
        user = get_current_user()
        if user and not (user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)):
            from core.managers import get_current_tenant
            profile = user.asset_holder_profiles.filter(tenant=get_current_tenant()).first() if get_current_tenant() else user.asset_holder_profiles.first()
            if profile and profile.tenant:
                instance.tenant = profile.tenant
        if commit:
            instance.save()
            self.save_m2m()
        return instance



class AlertRuleForm(forms.ModelForm):
    # Alert types whose threshold_value is a *days* horizon rather than a unit count.
    _DAYS_ALERT_TYPES = {
        AlertRule.ALERT_TYPE_UPCOMING_EOL,
        AlertRule.ALERT_TYPE_LICENSE_EXPIRY,
        AlertRule.ALERT_TYPE_RENEWAL_DUE,
        AlertRule.ALERT_TYPE_WARRANTY_EXPIRY,
        AlertRule.ALERT_TYPE_AUDIT_OVERDUE,
    }

    class Meta:
        model = AlertRule
        fields = [
            'name', 'description', 'alert_type', 'threshold_value', 'severity',
            'is_active', 'is_muted', 'renotify_interval_days', 'channels', 'tenant',
        ]
        widgets = {
            'channels': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'tenant': forms.Select(attrs={'class': 'form-select'})
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = get_current_user()
        if user and not (user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)):
            if 'tenant' in self.fields:
                self.fields.pop('tenant')

        # Make the threshold label/help reflect what the number actually means
        # for the selected alert type (days horizon vs. unit count).
        alert_type = (
            self.data.get('alert_type')
            or self.initial.get('alert_type')
            or getattr(self.instance, 'alert_type', None)
        )
        threshold = self.fields['threshold_value']
        if alert_type in self._DAYS_ALERT_TYPES:
            threshold.label = _('Days horizon')
            threshold.help_text = _('Alert when the date is within this many days.')
        elif alert_type == AlertRule.ALERT_TYPE_LOW_STOCK:
            threshold.label = _('Stock threshold (units)')
            threshold.help_text = _('Alert when available stock is at or below this many units '
                                    '(per-item minimum quantity overrides this when set).')
        else:
            threshold.help_text = _('Limit count or days horizon, depending on alert type.')

        self.fields['renotify_interval_days'].label = _('Re-notify every (days)')

    def save(self, commit=True):
        instance = super().save(commit=False)
        user = get_current_user()
        if user and not (user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)):
            from core.managers import get_current_tenant
            profile = user.asset_holder_profiles.filter(tenant=get_current_tenant()).first() if get_current_tenant() else user.asset_holder_profiles.first()
            if profile and profile.tenant:
                instance.tenant = profile.tenant
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class NotificationChannelForm(forms.ModelForm):
    """Channel config via typed, per-type fields rather than a raw JSON blob.

    The model stores everything in a single ``config`` JSONField, but users
    should never have to hand-build that JSON. We expose friendly fields
    (webhook URL, recipient list, recipient users) and assemble ``config``
    on save. ``form-toggles.ts`` shows only the fields relevant to the
    selected channel type.
    """

    webhook_url = forms.URLField(
        required=False,
        label=_('Incoming webhook URL'),
        widget=forms.URLInput(attrs={'placeholder': 'https://hooks.slack.com/services/...'}),
        help_text=_('Paste the incoming-webhook URL from Slack or Microsoft Teams.'),
    )
    email_recipients = forms.CharField(
        required=False,
        label=_('Recipient email addresses'),
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'alice@example.com, bob@example.com'}),
        help_text=_('Comma- or newline-separated email addresses.'),
    )
    in_app_recipient_users = forms.ModelMultipleChoiceField(
        required=False,
        queryset=None,
        label=_('Specific recipients'),
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        help_text=_("Optional. Leave empty to notify every member of this channel's tenant."),
    )

    field_order = [
        'name', 'channel_type',
        'webhook_url', 'email_recipients', 'in_app_recipient_users',
        'enabled', 'tenant',
    ]

    class Meta:
        model = NotificationChannel
        fields = ['name', 'channel_type', 'enabled', 'tenant']
        widgets = {
            'channel_type': forms.Select(attrs={'class': 'form-select'}),
            'tenant': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        User = get_user_model()

        user = get_current_user()
        is_admin = bool(user and (user.is_superuser or getattr(user, 'is_staff', False)))

        # Scope the in-app recipient choices: admins see everyone, tenant
        # users see only their own tenant's members.
        if is_admin:
            self.fields['in_app_recipient_users'].queryset = User.objects.filter(is_active=True)
        else:
            from core.managers import get_current_tenant
            tenant = get_current_tenant()
            if tenant:
                self.fields['in_app_recipient_users'].queryset = User.objects.filter(
                    asset_holder_profiles__tenant=tenant, is_active=True
                ).distinct()
            else:
                self.fields['in_app_recipient_users'].queryset = User.objects.none()
            if 'tenant' in self.fields:
                self.fields.pop('tenant')

        # Pre-fill typed fields from the stored config JSON (edit view).
        config = (self.instance.config or {}) if self.instance else {}
        if config:
            self.initial.setdefault('webhook_url', config.get('webhook_url', ''))
            recipients = config.get('recipients') or []
            if recipients:
                self.initial.setdefault('email_recipients', '\n'.join(recipients))
            recipient_users = config.get('recipient_users') or []
            if recipient_users:
                self.initial.setdefault('in_app_recipient_users', recipient_users)

    def clean(self):
        import re
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as DjangoValidationError

        cleaned = super().clean()
        channel_type = cleaned.get('channel_type')
        config = {}

        if channel_type == NotificationChannel.TYPE_EMAIL:
            raw = cleaned.get('email_recipients') or ''
            parts = [p.strip() for p in re.split(r'[,\n;]+', raw) if p.strip()]
            if not parts:
                self.add_error('email_recipients', _('Enter at least one recipient email address.'))
            else:
                bad = []
                for addr in parts:
                    try:
                        validate_email(addr)
                    except DjangoValidationError:
                        bad.append(addr)
                if bad:
                    self.add_error(
                        'email_recipients',
                        _('Invalid email address(es): %(bad)s') % {'bad': ', '.join(bad)},
                    )
                else:
                    config['recipients'] = parts

        elif channel_type in (NotificationChannel.TYPE_SLACK, NotificationChannel.TYPE_TEAMS):
            url = (cleaned.get('webhook_url') or '').strip()
            if not url:
                self.add_error('webhook_url', _('This channel type requires an incoming webhook URL.'))
            else:
                config['webhook_url'] = url

        elif channel_type == NotificationChannel.TYPE_IN_APP:
            users = cleaned.get('in_app_recipient_users')
            if users:
                config['recipient_users'] = [u.pk for u in users]
            # Empty is valid — delivery falls back to all tenant members.

        self._assembled_config = config
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.config = getattr(self, '_assembled_config', {}) or {}
        user = get_current_user()
        if user and not (user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)):
            from core.managers import get_current_tenant
            profile = user.asset_holder_profiles.filter(tenant=get_current_tenant()).first() if get_current_tenant() else user.asset_holder_profiles.first()
            if profile and profile.tenant:
                instance.tenant = profile.tenant
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ObjectChangeFilterForm(FilterForm):
    from core.filters import ObjectChangeFilterSet
    filterset_class = ObjectChangeFilterSet





