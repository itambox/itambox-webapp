from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div, Row, Column, Fieldset
from django.urls import reverse
from core.forms import FilterForm, CrispyFormMixin, scope_tenant_field, scope_tenant_group_field
from organization.models import Tenant, TenantGroup, AssetHolder, Location, CostCenter
from assets.models import Asset
from django.db import models as db_models
from .models import Provider, Subscription, SubscriptionAssignment
from .filters import SubscriptionFilterSet, ProviderFilterSet


from extras.customfields import CustomFieldModelFormMixin

class ProviderForm(CrispyFormMixin, forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Tenant"),
    )
    tenant_group = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Tenant Group"),
    )

    class Meta:
        model = Provider
        fields = (
            'name', 'slug', 'tenant', 'tenant_group', 'account_id', 'portal_url', 'admin_notes', 'is_active', 'tags',
        )
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control',
                                          'data-slug-help': ''}),
            'account_id': forms.TextInput(attrs={'class': 'form-control'}),
            'portal_url': forms.URLInput(attrs={'class': 'form-control'}),
            'admin_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }
        help_texts = {
            'slug': _("Changing the slug may break existing import references that use it as a natural key."),
        }

    def clean(self):
        cleaned_data = super().clean()
        tenant = cleaned_data.get('tenant')
        tenant_group = cleaned_data.get('tenant_group')
        if tenant and tenant_group:
            raise forms.ValidationError(
                _("A provider may be scoped to a Tenant or a Tenant Group, but not both.")
            )
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # autoset_when_single=False: Provider scopes to a tenant OR a tenant group
        # (or global) — auto-setting the tenant would break the XOR clean().
        scope_tenant_field(self, autoset_when_single=False)
        scope_tenant_group_field(self)
        # Keep `tenant` optional (tenant XOR group, or global). The global BaseForm
        # patch (core/apps.py) already skips forms that also declare `tenant_group`;
        # this explicit reset is the load-bearing guard for the XOR clean().
        self.fields['tenant'].required = False

        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('subscriptions:provider_list')

        self.helper.layout = Layout(
            Fieldset(
                _('Identity'),
                Div(
                    Div('name', css_class='col-md-6'),
                    Div('slug', css_class='col-md-6'),
                    css_class='row',
                ),
                Div(
                    Div('portal_url', css_class='col-md-12'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Scope'),
                Div(
                    Div('account_id', css_class='col-md-4'),
                    Div('tenant', css_class='col-md-4'),
                    Div('tenant_group', css_class='col-md-4'),
                    css_class='row',
                ),
                Div(
                    Div('is_active', css_class='col-md-4'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Notes & Tags'),
                'admin_notes',
                'tags',
            ),
            *self.action_buttons(cancel_url),
        )


class SubscriptionForm(CrispyFormMixin, CustomFieldModelFormMixin, forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Tenant"),
    )
    cost_center = forms.ModelChoiceField(
        queryset=CostCenter.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Cost Center"),
    )

    class Meta:
        model = Subscription
        fields = (
            'name', 'slug', 'provider', 'type', 'status',
            'start_date', 'renewal_date', 'term_months',
            'renewal_cost', 'currency', 'billing_cycle',
            'licensed_quantity', 'contract_reference', 'cost_center',
            'cancellation_date', 'owner',
            'auto_renewal',
            'description', 'notes',
            'tags', 'tenant',
        )
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'provider': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'type': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'renewal_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'renewal_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 3}),
            'billing_cycle': forms.Select(attrs={'class': 'form-select'}),
            'term_months': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'licensed_quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'contract_reference': forms.TextInput(attrs={'class': 'form-control'}),
            'cancellation_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'owner': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }
        help_texts = {
            'slug': _("Changing the slug may break existing import references that use it as a natural key."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_tenant_field(self)
        # Rescope the tenant-owned `cost_center` FK per request (import-frozen
        # unscoped — would expose/permit another tenant's cost center).
        self.fields['cost_center'].queryset = CostCenter.objects.all()

        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('subscriptions:subscription_list')

        self.helper.layout = Layout(
            Fieldset(
                _('Identity'),
                Div(
                    Div('name', css_class='col-md-6'),
                    Div('slug', css_class='col-md-6'),
                    css_class='row',
                ),
                Div(
                    Div('provider', css_class='col-md-6'),
                    Div('type', css_class='col-md-3'),
                    Div('status', css_class='col-md-3'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Dates & Term'),
                Div(
                    Div('start_date', css_class='col-md-4'),
                    Div('renewal_date', css_class='col-md-4'),
                    Div('term_months', css_class='col-md-4'),
                    css_class='row',
                ),
                Div(
                    Div('cancellation_date', css_class='col-md-4'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Financial'),
                Div(
                    Div('renewal_cost', css_class='col-md-4'),
                    Div('currency', css_class='col-md-3'),
                    Div('billing_cycle', css_class='col-md-5'),
                    css_class='row',
                ),
                Div(
                    Div('licensed_quantity', css_class='col-md-4'),
                    Div('contract_reference', css_class='col-md-4'),
                    Div('cost_center', css_class='col-md-4'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Policy'),
                Div(
                    Div('auto_renewal', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Scope'),
                Div(
                    Div('owner', css_class='col-md-6'),
                    Div('tenant', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Notes & Tags'),
                'description',
                'notes',
                'tags',
            ),
            *self.action_buttons(cancel_url),
        )
        self.append_custom_fields_to_layout()


class ProviderFilterForm(FilterForm):
    filterset_class = ProviderFilterSet


class SubscriptionFilterForm(FilterForm):
    filterset_class = SubscriptionFilterSet


class SubscriptionAssignmentForm(forms.ModelForm):
    subscription = forms.ModelChoiceField(
        queryset=Subscription.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Subscription")
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label=_("Notes")
    )

    class Meta:
        model = SubscriptionAssignment
        fields = ['subscription', 'notes']

    def clean(self):
        cleaned_data = super().clean()
        subscription = cleaned_data.get('subscription')
        if subscription and self.content_type and self.object_id:
            if SubscriptionAssignment.objects.filter(
                subscription=subscription,
                content_type=self.content_type,
                object_id=self.object_id
            ).exists():
                raise forms.ValidationError(
                    _("This subscription is already assigned to this object.")
                )
        return cleaned_data

    def __init__(self, *args, **kwargs):
        content_type = kwargs.pop('content_type', None)
        object_id = kwargs.pop('object_id', None)
        super().__init__(*args, **kwargs)
        self.content_type = content_type
        self.object_id = object_id

        if content_type and object_id:
            try:
                target_obj = content_type.model_class().objects.get(id=object_id)
                if hasattr(target_obj, 'tenant') and target_obj.tenant:
                    # Filter subscriptions to active ones that belong to the same tenant or are global
                    self.fields['subscription'].queryset = Subscription.objects.filter(
                        db_models.Q(tenant=target_obj.tenant) | db_models.Q(tenant__isnull=True),
                        status='active'
                    )
            except Exception:
                pass

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'subscription',
            'notes',
        )
        
        button_text = 'Assign'
        self.helper.layout.append(
            HTML('<div class="mt-4"></div>')
        )
        self.helper.layout.append(
            Submit('submit', button_text, css_class='btn btn-primary')
        )
        self.helper.layout.append(
            HTML('<button type="button" class="btn btn-outline-secondary ms-2" data-bs-dismiss="modal">Cancel</button>')
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.content_type and self.object_id:
            instance.content_type = self.content_type
            instance.object_id = self.object_id
        if commit:
            instance.save()
        return instance


class SubscriptionRenewForm(forms.Form):
    renewal_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label=_("Next Renewal Date"),
        required=True
    )
    renewal_cost = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        label=_("Renewal Cost"),
        required=False
    )

    def __init__(self, *args, **kwargs):
        subscription = kwargs.pop('subscription', None)
        super().__init__(*args, **kwargs)
        if subscription:
            self.fields['renewal_cost'].initial = subscription.renewal_cost
            if subscription.renewal_date:
                from datetime import timedelta
                cycle = subscription.billing_cycle
                current_date = subscription.renewal_date
                if cycle == 'monthly':
                    self.fields['renewal_date'].initial = current_date + timedelta(days=30)
                elif cycle == 'quarterly':
                    self.fields['renewal_date'].initial = current_date + timedelta(days=91)
                elif cycle == 'biannual':
                    self.fields['renewal_date'].initial = current_date + timedelta(days=182)
                else:
                    self.fields['renewal_date'].initial = current_date + timedelta(days=365)
            else:
                from django.utils import timezone
                self.fields['renewal_date'].initial = timezone.now().date()

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'renewal_date',
            'renewal_cost',
        )


class SubscriptionCancelForm(forms.Form):
    cancellation_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label=_("Cancellation Date"),
        required=True
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label=_("Cancellation Reason"),
        required=False,
        help_text=_("Optional reason notes to log on the subscription.")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone
        self.fields['cancellation_date'].initial = timezone.now().date()

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'cancellation_date',
            'reason',
        )


class SubscriptionCheckoutForm(forms.Form):
    TARGET_CHOICES = [
        ('holder', 'Employee / Asset Holder'),
        ('asset', 'Hardware Asset'),
        ('location', 'Location'),
    ]

    target_type = forms.ChoiceField(
        choices=TARGET_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to")
    )
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Asset Holder")
    )
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.exclude(status__type='undeployable').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Hardware Asset")
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Location")
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=False,
        label=_("Notes")
    )

    def clean(self):
        cleaned_data = super().clean()
        target_type = cleaned_data.get('target_type')
        holder = cleaned_data.get('assigned_holder')
        asset = cleaned_data.get('asset')
        location = cleaned_data.get('location')

        if target_type == 'holder' and not holder:
            raise forms.ValidationError(_("Must select an Asset Holder."), code='holder_required')
        if target_type == 'asset' and not asset:
            raise forms.ValidationError(_("Must select a Hardware Asset."), code='asset_required')
        if target_type == 'location' and not location:
            raise forms.ValidationError(_("Must select a Location."), code='location_required')
        if not target_type:
            raise forms.ValidationError(_("Must select a target type."), code='target_type_required')

        # Check for duplicate assignment
        target_obj = None
        if target_type == 'holder':
            target_obj = holder
        elif target_type == 'asset':
            target_obj = asset
        elif target_type == 'location':
            target_obj = location

        if target_obj and getattr(self, 'subscription', None):
            from django.contrib.contenttypes.models import ContentType
            content_type = ContentType.objects.get_for_model(target_obj)
            if SubscriptionAssignment.objects.filter(
                subscription=self.subscription,
                content_type=content_type,
                object_id=target_obj.pk
            ).exists():
                raise forms.ValidationError(
                    _("This subscription is already assigned to %(target)s.") % {"target": target_obj}
                )

        return cleaned_data

    def __init__(self, *args, **kwargs):
        subscription = kwargs.pop('subscription', None)
        self.subscription = subscription
        super().__init__(*args, **kwargs)
        
        # If tenant is restricted, filter candidates
        if subscription and subscription.tenant:
            self.fields['assigned_holder'].queryset = AssetHolder.objects.filter(tenant=subscription.tenant).order_by('last_name', 'first_name')
            self.fields['asset'].queryset = Asset.objects.filter(tenant=subscription.tenant).exclude(status__type='undeployable').order_by('name')
            self.fields['location'].queryset = Location.objects.filter(tenant=subscription.tenant).order_by('name')
            
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'target_type',
            'assigned_holder',
            'asset',
            'location',
            'notes',
        )

