from django import forms
from django.utils.translation import gettext_lazy as _
from .models import Tag, CustomField, CustomFieldset, ConfigContext
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div, Field, Fieldset, Row, Column
from django.urls import reverse
from core.forms import FilterForm, ColorFieldFormMixin
from .filters import TagFilter

class TagForm(ColorFieldFormMixin, forms.ModelForm):
    # color is handled by ColorFieldFormMixin (prepends '#' on init, strips on clean)
    color = forms.CharField(
        max_length=7,
        required=False,
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'})
    )

    class Meta:
        model = Tag
        fields = ['name', 'slug', 'color', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
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
            'regions': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'sites': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'locations': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'tenants': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
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
            Fieldset(
                'Scope (optional)',
                Row(
                    Column('regions', css_class='col-md-6'),
                    Column('sites', css_class='col-md-6'),
                    css_class='row g-3',
                ),
                Row(
                    Column('locations', css_class='col-md-6'),
                    Column('tenants', css_class='col-md-6'),
                    css_class='row g-3',
                ),
            ),
            Fieldset(
                'Configuration Data',
                'data',
            ),
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


# =============================================================================
# Domain forms — moved from core/forms/__init__.py (audit B2).
# These depend on extras.models and therefore live here rather than in the
# framework layer.  The old import path (core.forms.XxxForm) has been
# repointed to extras.forms in every consumer.
# =============================================================================

import json as _json

from django.contrib.contenttypes.models import ContentType
from crispy_forms.layout import Layout, Field, HTML, Div, Submit, Row, Column, Fieldset
from .models import WebhookEndpoint, EventRule, Event, LabelTemplate, ReportTemplate, ScheduledReport, AlertRule, NotificationChannel
from itambox.middleware import get_current_user


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


SLACK_PAYLOAD_PRESET = _json.dumps({
    "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": "ITAMbox Event: {{ event }}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": "*Model:*\n{{ model }}"},
            {"type": "mrkdwn", "text": "*Object ID:*\n{{ object_id }}"}
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": "```{{ data | tojson }}```"}},
    ]
}, indent=2)

TEAMS_PAYLOAD_PRESET = _json.dumps({
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
        label=_('Payload Preset'),
        help_text=_('Select a preset to pre-fill the payload template above'),
    )

    class Meta:
        model = WebhookEndpoint
        fields = ['name', 'url', 'http_method', 'headers', 'payload_preset', 'secret', 'enabled', 'retry_count', 'retry_backoff', 'tenant']
        widgets = {
            'headers': forms.Textarea(attrs={'rows': 4}),
            'tenant': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = get_current_user()
        is_admin = bool(user and (user.is_superuser or getattr(user, 'is_staff', False)))
        if not is_admin and 'tenant' in self.fields:
            self.fields.pop('tenant')

        if self.instance and self.instance.headers:
            self.initial['headers'] = _json.dumps(self.instance.headers, indent=2)

        # BUG FIX: show the decrypted secret on edit so we don't re-encrypt
        # the stored "enc$..." ciphertext as if it were a plaintext value.
        if self.instance and self.instance.pk and self.instance.secret:
            self.initial['secret'] = self.instance.secret_decrypted

        self.helper = FormHelper()
        tenant_row = (
            Row(
                Column('tenant', css_class='col-md-6'),
                css_class='row g-3',
            )
            if is_admin else None
        )
        layout_fields = [
            Fieldset(
                _('Identity'),
                Row(
                    Column('name', css_class='col-md-6'),
                    Column('url', css_class='col-md-6'),
                    css_class='row g-3',
                ),
                Row(
                    Column('http_method', css_class='col-md-4'),
                    Column('enabled', css_class='col-md-4'),
                    Column('secret', css_class='col-md-4'),
                    css_class='row g-3',
                ),
            ),
            Fieldset(
                _('Payload'),
                'payload_preset',
                'headers',
            ),
            Fieldset(
                _('Retry'),
                Row(
                    Column('retry_count', css_class='col-md-6'),
                    Column('retry_backoff', css_class='col-md-6'),
                    css_class='row g-3',
                ),
            ),
        ]
        if tenant_row is not None:
            layout_fields.append(tenant_row)
        layout_fields += [
            HTML('<div class="mt-4"></div>'),
            Submit('submit', _('Save Webhook Endpoint'), css_class='btn btn-primary'),
            HTML(
                '<a href="{% url \'webhookendpoint_list\' %}" class="btn btn-outline-secondary ms-2" '
                'data-no-dirty-track="true">' + str(_('Cancel')) + '</a>'
            ),
        ]
        self.helper.layout = Layout(*layout_fields)

    def clean_headers(self):
        data = self.cleaned_data['headers']
        if isinstance(data, str):
            try:
                return _json.loads(data)
            except _json.JSONDecodeError:
                raise forms.ValidationError(_('Headers must be valid JSON.'))
        return data


class EventRuleForm(forms.ModelForm):
    events = forms.MultipleChoiceField(
        choices=Event.ACTION_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label=_('Trigger Events'),
        help_text=_('Fire this rule when any of the selected change types occur on the target model.'),
    )
    payload_preset = forms.ChoiceField(
        choices=PAYLOAD_PRESET_CHOICES,
        required=False,
        label=_('Payload Preset'),
        help_text=_('Select a preset to pre-fill the action config'),
    )

    class Meta:
        model = EventRule
        fields = ['name', 'model', 'events', 'action_type', 'webhook', 'conditions', 'action_config', 'enabled', 'tenant']
        widgets = {
            'conditions': forms.Textarea(attrs={'rows': 3}),
            'action_config': forms.Textarea(attrs={'rows': 4}),
            'tenant': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = get_current_user()
        is_admin = bool(user and (user.is_superuser or getattr(user, 'is_staff', False)))
        if not is_admin and 'tenant' in self.fields:
            self.fields.pop('tenant')

        # Only models that emit Events are selectable — others would never trigger the rule.
        self.fields['model'].queryset = logged_content_types()
        self.fields['model'].label = _('Target Model')
        self.fields['webhook'].queryset = WebhookEndpoint.objects.filter(enabled=True)
        self.fields['webhook'].label = _('Webhook Endpoint')
        self.fields['webhook'].help_text = _(
            'Required for Webhook rules. Manage endpoints under Webhook Endpoints. '
            'Leave blank only if you supply a "url" in Action Configuration below.'
        )
        self.fields['conditions'].required = False
        self.fields['action_config'].required = False
        if self.instance and self.instance.pk:
            if self.instance.events:
                self.initial['events'] = self.instance.events
            if self.instance.conditions:
                self.initial['conditions'] = _json.dumps(self.instance.conditions, indent=2)
            if self.instance.action_config:
                self.initial['action_config'] = _json.dumps(self.instance.action_config, indent=2)

        self.helper = FormHelper()
        layout_fields = [
            Fieldset(
                _('Identity'),
                Row(
                    Column('name', css_class='col-md-6'),
                    Column('enabled', css_class='col-md-6'),
                    css_class='row g-3',
                ),
            ),
            Fieldset(
                _('Trigger'),
                Row(
                    Column('model', css_class='col-md-6'),
                    Column('action_type', css_class='col-md-6'),
                    css_class='row g-3',
                ),
                'events',
            ),
            Fieldset(
                _('Action'),
                'webhook',
                'payload_preset',
                'conditions',
                'action_config',
            ),
        ]
        if is_admin:
            layout_fields.append(
                Row(Column('tenant', css_class='col-md-6'), css_class='row g-3')
            )
        layout_fields += [
            HTML('<div class="mt-4"></div>'),
            Submit('submit', _('Save Event Rule'), css_class='btn btn-primary'),
            HTML(
                '<a href="{% url \'eventrule_list\' %}" class="btn btn-outline-secondary ms-2" '
                'data-no-dirty-track="true">' + str(_('Cancel')) + '</a>'
            ),
        ]
        self.helper.layout = Layout(*layout_fields)

    def clean_conditions(self):
        data = self.cleaned_data['conditions']
        if isinstance(data, str):
            if not data.strip():
                return {}
            try:
                return _json.loads(data)
            except _json.JSONDecodeError:
                raise forms.ValidationError('Conditions must be valid JSON.')
        return data or {}

    def clean_action_config(self):
        data = self.cleaned_data['action_config']
        if isinstance(data, str):
            if not data.strip():
                return {}
            try:
                return _json.loads(data)
            except _json.JSONDecodeError:
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            Fieldset(
                _('Identity'),
                Row(
                    Column('name', css_class='col-md-8'),
                    Column('barcode_format', css_class='col-md-4'),
                    css_class='row g-3',
                ),
                'description',
            ),
            Fieldset(
                _('Page Size'),
                Row(
                    Column('page_width', css_class='col-md-6'),
                    Column('page_height', css_class='col-md-6'),
                    css_class='row g-3',
                ),
            ),
            Fieldset(
                _('Template'),
                'template_code',
            ),
            HTML('<div class="mt-4"></div>'),
            Submit('submit', _('Save Label Template'), css_class='btn btn-primary'),
            HTML(
                '<a href="{% url \'labeltemplate_list\' %}" class="btn btn-outline-secondary ms-2" '
                'data-no-dirty-track="true">' + str(_('Cancel')) + '</a>'
            ),
        )


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

        is_admin = bool(user and (user.is_superuser or getattr(user, 'is_staff', False)))
        self.helper = FormHelper()
        admin_fieldset_fields = []
        if is_admin:
            admin_fieldset_fields = [
                Row(
                    Column('tenant', css_class='col-md-6'),
                    Column('filter_tenants', css_class='col-md-6'),
                    css_class='row g-3',
                ),
            ]
        layout_fields = [
            Fieldset(
                _('Identity'),
                Row(
                    Column('name', css_class='col-md-6'),
                    Column('report', css_class='col-md-6'),
                    css_class='row g-3',
                ),
            ),
            Fieldset(
                _('Schedule'),
                Row(
                    Column('frequency', css_class='col-md-4'),
                    Column('cron_expression', css_class='col-md-4'),
                    Column('start_time', css_class='col-md-4'),
                    css_class='row g-3',
                ),
            ),
            Fieldset(
                _('Delivery'),
                'recipients',
                'channels',
                Row(
                    Column('format', css_class='col-md-4'),
                    Column('save_to_archive', css_class='col-md-4'),
                    Column('is_active', css_class='col-md-4'),
                    css_class='row g-3',
                ),
            ),
        ]
        if admin_fieldset_fields:
            layout_fields.append(Fieldset(_('Scope'), *admin_fieldset_fields))
        layout_fields += [
            HTML('<div class="mt-4"></div>'),
            Submit('submit', _('Save Scheduled Report'), css_class='btn btn-primary'),
            HTML(
                '<a href="{% url \'scheduledreport_list\' %}" class="btn btn-outline-secondary ms-2" '
                'data-no-dirty-track="true">' + str(_('Cancel')) + '</a>'
            ),
        ]
        self.helper.layout = Layout(*layout_fields)

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

        is_admin = bool(user and (user.is_superuser or getattr(user, 'is_staff', False)))
        self.helper = FormHelper()
        layout_fields = [
            Fieldset(
                _('Identity'),
                Row(
                    Column('name', css_class='col-md-8'),
                    Column('severity', css_class='col-md-4'),
                    css_class='row g-3',
                ),
                'description',
            ),
            Fieldset(
                _('Alert Configuration'),
                Row(
                    Column('alert_type', css_class='col-md-6'),
                    Column('threshold_value', css_class='col-md-6'),
                    css_class='row g-3',
                ),
                Row(
                    Column('renotify_interval_days', css_class='col-md-4'),
                    Column('is_active', css_class='col-md-4'),
                    Column('is_muted', css_class='col-md-4'),
                    css_class='row g-3',
                ),
            ),
            Fieldset(
                _('Notifications'),
                'channels',
            ),
        ]
        if is_admin:
            layout_fields.append(
                Row(Column('tenant', css_class='col-md-6'), css_class='row g-3')
            )
        layout_fields += [
            HTML('<div class="mt-4"></div>'),
            Submit('submit', _('Save Alert Rule'), css_class='btn btn-primary'),
            HTML(
                '<a href="{% url \'alertrule_list\' %}" class="btn btn-outline-secondary ms-2" '
                'data-no-dirty-track="true">' + str(_('Cancel')) + '</a>'
            ),
        ]
        self.helper.layout = Layout(*layout_fields)

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

