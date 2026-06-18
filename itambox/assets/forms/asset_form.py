from django import forms
from django.urls import reverse
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column, Fieldset, Div

from core.forms import CrispyFormMixin
from extras.models import Tag, CustomField
from organization.models import Location
from ..models import Asset, AssetType, AssetRole, StatusLabel

from .fields import StatusModelChoiceField


class AssetForm(CrispyFormMixin, forms.ModelForm):
    asset_type = forms.ModelChoiceField(
        queryset=AssetType.objects.select_related('manufacturer').all(),
        label=_("Asset Type"),
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'data-tom-select': '',
            'hx-post': '',
            'hx-trigger': 'change',
            'hx-target': 'closest form',
            'hx-swap': 'outerHTML',
            'hx-vals': '{"_reload": "1"}',
            'hx-include': 'closest form',
        })
    )
    asset_role = forms.ModelChoiceField(
        queryset=AssetRole.objects.all(),
        label=_("Asset Role"),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    status = StatusModelChoiceField(
        queryset=StatusLabel.objects.all(),
        label=_("Status"),
        required=True,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    purchase_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=False
    )
    requestable = forms.ChoiceField(
        choices=[
            ('', 'Inherit from Asset Type (Default)'),
            ('true', 'Yes (Force Requestable)'),
            ('false', 'No (Force Unrequestable)'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Requestable Status")
    )

    class Meta:
        model = Asset
        fields = [
            'name', 'asset_tag', 'serial_number', 'asset_type',
            'asset_role', 'status', 'location', 'tenant',
            'purchase_date',
            'purchase_cost', 'salvage_value', 'currency', 'order_number', 'supplier',
            'purchase_order_line', 'cost_center',
            'in_service_date', 'depreciation_override',
            'notes', 'tags', 'requestable'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'asset_tag': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to auto-generate'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'purchase_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'salvage_value': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'order_number': forms.TextInput(attrs={'class': 'form-control'}),
            'in_service_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'depreciation_override': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tenant': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'supplier': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'purchase_order_line': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'cost_center': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }

    def clean_status(self):
        status = self.cleaned_data.get('status')
        if isinstance(status, str):
            from django.db.models import Q
            status_obj = StatusLabel.objects.filter(Q(slug=status) | Q(name__iexact=status)).first()
            if status_obj:
                return status_obj
            raise forms.ValidationError(_("Invalid status label: %(status)s") % {"status": status})
        return status

    def clean_requestable(self):
        val = self.cleaned_data.get('requestable')
        if val == 'true':
            return True
        elif val == 'false':
            return False
        return None

    def __init__(self, *args, **kwargs):
        request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        cancel_url = reverse('assets:asset_list')

        asset_type_id = None
        if self.data and self.data.get('asset_type'):
            try:
                asset_type_id = int(self.data.get('asset_type'))
            except (ValueError, TypeError):
                pass
        elif request and request.GET.get('asset_type'):
            try:
                asset_type_id = int(request.GET.get('asset_type'))
            except (ValueError, TypeError):
                pass
        elif self.initial and self.initial.get('asset_type'):
            asset_type_val = self.initial.get('asset_type')
            if isinstance(asset_type_val, AssetType):
                asset_type_id = asset_type_val.pk
            else:
                asset_type_id = asset_type_val
            asset_type_id = self.instance.asset_type.pk

        if self.instance and self.instance.pk:
            if self.instance.requestable is None:
                self.initial['requestable'] = ''
            elif self.instance.requestable is True:
                self.initial['requestable'] = 'true'
            else:
                self.initial['requestable'] = 'false'

        # Ensure asset_tag is required in the form
        self.fields['asset_tag'].required = True

        from django.utils.safestring import mark_safe
        # Configure quick-add buttons inside labels instead of layout divs
        if 'asset_type' in self.fields:
            url_type = reverse('assets:assettype_create') + '?_quickadd=1'
            self.fields['asset_type'].label = mark_safe(
                f'Asset Type <button type="button" class="btn btn-link p-0 ms-1 align-baseline border-0 bg-transparent text-primary" style="font-size: 1.1rem; line-height: 1;" title="Add new Asset Type" hx-get="{url_type}" hx-target="#modal-placeholder"><i class="mdi mdi-plus-circle-outline"></i></button>'
            )

        if 'asset_role' in self.fields:
            url_role = reverse('assets:assetrole_create') + '?_quickadd=1'
            self.fields['asset_role'].label = mark_safe(
                f'Asset Role <button type="button" class="btn btn-link p-0 ms-1 align-baseline border-0 bg-transparent text-primary" style="font-size: 1.1rem; line-height: 1;" title="Add new Asset Role" hx-get="{url_role}" hx-target="#modal-placeholder"><i class="mdi mdi-plus-circle-outline"></i></button>'
            )

        if 'location' in self.fields:
            url_loc = reverse('organization:location_create') + '?_quickadd=1'
            self.fields['location'].label = mark_safe(
                f'Location <button type="button" class="btn btn-link p-0 ms-1 align-baseline border-0 bg-transparent text-primary" style="font-size: 1.1rem; line-height: 1;" title="Add new Location" hx-get="{url_loc}" hx-target="#modal-placeholder"><i class="mdi mdi-plus-circle-outline"></i></button>'
            )


        # Hook up HTMX trigger on tenant field to update suggestion on change
        if 'tenant' in self.fields:
            self.fields['tenant'].widget.attrs.update({
                'hx-post': '',
                'hx-trigger': 'change',
                'hx-target': 'closest form',
                'hx-swap': 'outerHTML',
                'hx-vals': '{"_reload": "1"}',
                'hx-include': 'closest form',
            })

        # Calculate suggested tag based on selected tenant and asset_type
        # 1. Resolve tenant
        from organization.models import Tenant
        selected_tenant = None
        raw_tenant = None
        if self.is_bound and self.data.get('tenant'):
            raw_tenant = self.data.get('tenant')
        elif self.initial.get('tenant'):
            raw_tenant = self.initial.get('tenant')
        elif self.instance and self.instance.tenant:
            raw_tenant = self.instance.tenant

        if raw_tenant:
            if isinstance(raw_tenant, Tenant):
                selected_tenant = raw_tenant
            else:
                try:
                    selected_tenant = Tenant.objects.get(pk=raw_tenant)
                except (Tenant.DoesNotExist, ValueError, TypeError):
                    pass

        # 2. Resolve asset_type object
        selected_type = None
        if asset_type_id:
            try:
                selected_type = AssetType.objects.get(pk=asset_type_id)
            except AssetType.DoesNotExist:
                pass

        # 3. Resolve sequence preview
        from django.utils.safestring import mark_safe
        from ..models import AssetTagSequence
        dummy_asset = Asset(tenant=selected_tenant, asset_type=selected_type)
        seq = AssetTagSequence.resolve_sequence_for_asset(dummy_asset)
        if seq:
            suggested_tag = seq.next_tag_preview
            self.fields['asset_tag'].help_text = mark_safe(
                f'<span class="text-muted small">Suggested: <a href="#" class="text-primary font-monospace" data-fill-target="id_asset_tag" data-fill-value="{suggested_tag}">{suggested_tag}</a></span>'
            )
        else:
            self.fields['asset_tag'].help_text = mark_safe(
                f'<span class="text-muted small">No active tag sequence found for this scope.</span>'
            )

        # Default the asset role from the selected asset type if not already set
        if not self.instance.pk and asset_type_id:
            current_role = None
            if self.data and 'asset_role' in self.data:
                current_role = self.data.get('asset_role')
            elif self.initial and 'asset_role' in self.initial:
                current_role = self.initial.get('asset_role')
                
            if not current_role:
                try:
                    asset_type_obj = AssetType.objects.get(pk=asset_type_id)
                    if asset_type_obj.asset_role:
                        self.fields['asset_role'].initial = asset_type_obj.asset_role
                except AssetType.DoesNotExist:
                    pass

        # Per-device custom fields: globally-applicable Asset fields (not bound
        # to any fieldset) plus the selected asset type's fieldset fields that
        # target Asset.
        from django.contrib.contenttypes.models import ContentType
        asset_ct = ContentType.objects.get_for_model(Asset)
        custom_fields = CustomField.objects.filter(object_types=asset_ct, fieldsets__isnull=True)
        if asset_type_id:
            try:
                asset_type_obj = AssetType.objects.get(pk=asset_type_id)
                if asset_type_obj.custom_fieldset:
                    custom_fields = custom_fields | asset_type_obj.custom_fieldset.fields.filter(object_types=asset_ct)
            except AssetType.DoesNotExist:
                pass
        custom_fields = custom_fields.distinct()

        self.custom_field_keys = []
        for field in custom_fields:
            field_key = f"cf_{field.name}"
            self.custom_field_keys.append(field_key)

            initial_value = None
            if self.instance and self.instance.pk and self.instance.custom_field_data:
                initial_value = self.instance.custom_field_data.get(field.name)

            form_field = None
            if field.field_type == CustomField.FIELD_TYPE_TEXT:
                form_field = forms.CharField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value,
                    widget=forms.TextInput(attrs={'class': 'form-control'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_NUMBER:
                form_field = forms.DecimalField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value,
                    widget=forms.NumberInput(attrs={'class': 'form-control'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_DATE:
                form_field = forms.DateField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value,
                    widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_BOOLEAN:
                form_field = forms.BooleanField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value or False,
                    widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_SELECT:
                choice_lines = [line.strip() for line in (field.choices or '').split('\n') if line.strip()]
                choices = [('', '---------')] + [(choice, choice) for choice in choice_lines]
                form_field = forms.ChoiceField(
                    label=field.label,
                    required=field.required,
                    choices=choices,
                    initial=initial_value,
                    widget=forms.Select(attrs={'class': 'form-select'})
                )

            if form_field:
                self.fields[field_key] = form_field

        # Grouped, standardized section order: Identity -> Classification ->
        # Assignment -> Procurement & Financial -> Lifecycle -> Custom -> Notes.
        layout_elements = [
            Fieldset(
                'Identity',
                Div(
                    Div('name', css_class='col-md-6'),
                    Div('asset_tag', css_class='col-md-6'),
                    css_class='row'
                ),
                Div(
                    Div('serial_number', css_class='col-md-6'),
                    Div('status', css_class='col-md-6'),
                    css_class='row'
                ),
            ),
            Fieldset(
                'Classification',
                Div(
                    Div('asset_type', css_class='col-md-6'),
                    Div('asset_role', css_class='col-md-6'),
                    css_class='row'
                ),
            ),
            Fieldset(
                'Assignment',
                Div(
                    Div('location', css_class='col-md-6'),
                    Div('tenant', css_class='col-md-6'),
                    css_class='row'
                ),
            ),
            Fieldset(
                'Procurement & Financial',
                Div(
                    Div('purchase_date', css_class='col-md-4'),
                    Div('order_number', css_class='col-md-4'),
                    Div('supplier', css_class='col-md-4'),
                    css_class='row'
                ),
                Div(
                    Div('purchase_order_line', css_class='col-md-6'),
                    css_class='row'
                ),
                Div(
                    Div('purchase_cost', css_class='col-md-4'),
                    Div('currency', css_class='col-md-4'),
                    Div('salvage_value', css_class='col-md-4'),
                    css_class='row'
                ),
                Div(
                    Div('cost_center', css_class='col-md-6'),
                    css_class='row'
                ),
            ),
            Fieldset(
                'Lifecycle',
                Div(
                    Div('in_service_date', css_class='col-md-6'),
                    Div('depreciation_override', css_class='col-md-6'),
                    css_class='row'
                ),
            ),
        ]

        if self.custom_field_keys:
            cf_divs = []
            for i in range(0, len(self.custom_field_keys), 2):
                chunk = self.custom_field_keys[i:i+2]
                row_cols = [Div(key, css_class='col-md-6') for key in chunk]
                cf_divs.append(Div(*row_cols, css_class='row'))
            layout_elements.append(
                Fieldset(
                    'Custom Specifications',
                    *cf_divs,
                    css_class='mb-4 border p-3 rounded'
                )
            )

        layout_elements.append(
            Fieldset(
                'Notes & Tags',
                Div(
                    Div('tags', css_class='col-md-6'),
                    Div('requestable', css_class='col-md-6'),
                    css_class='row'
                ),
                'notes',
            )
        )

        layout_elements.extend(self.action_buttons(cancel_url))

        self.helper.layout = Layout(*layout_elements)

    def save(self, commit=True):
        instance = super().save(commit=False)

        custom_field_data = {}
        for key, value in self.cleaned_data.items():
            if key.startswith('cf_'):
                field_name = key[3:]
                if value is not None:
                    if isinstance(value, (int, float, bool)):
                        custom_field_data[field_name] = value
                    elif hasattr(value, 'isoformat'):
                        custom_field_data[field_name] = value.isoformat()
                    else:
                        custom_field_data[field_name] = str(value)
                else:
                    custom_field_data[field_name] = None

        instance.custom_field_data = custom_field_data

        if commit:
            instance.save()
            self.save_m2m()
        return instance
