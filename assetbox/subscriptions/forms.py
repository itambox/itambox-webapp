from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div, Row, Column
from django.urls import reverse
from core.forms import FilterForm
from organization.models import Tenant
from .models import Provider, Subscription
from .filters import SubscriptionFilterSet, ProviderFilterSet


class ProviderForm(forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Tenant"
    )

    class Meta:
        model = Provider
        fields = (
            'name', 'slug', 'account_id', 'portal_url', 'website',
            'contact_email', 'contact_phone', 'admin_notes',
            'support_contact', 'is_active', 'tags',
        )
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'account_id': forms.TextInput(attrs={'class': 'form-control'}),
            'portal_url': forms.URLInput(attrs={'class': 'form-control'}),
            'website': forms.URLInput(attrs={'class': 'form-control'}),
            'contact_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'contact_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'admin_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'support_contact': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('subscriptions:provider_list')

        self.helper.layout = Layout(
            Row(
                Column('name', css_class='col-md-6'),
                Column('slug', css_class='col-md-6'),
            ),
            Row(
                Column('portal_url', css_class='col-md-6'),
                Column('website', css_class='col-md-6'),
            ),
            Row(
                Column('account_id', css_class='col-md-6'),
                Column('is_active', css_class='col-md-6'),
            ),
            Row(
                Column('contact_email', css_class='col-md-6'),
                Column('contact_phone', css_class='col-md-6'),
            ),
            'admin_notes',
            'support_contact',
            'tags',
            HTML('<div class="mt-3 d-flex justify-content-between">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary">Cancel</a>'),
            HTML('</div>')
        )


class SubscriptionForm(forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Tenant"
    )

    class Meta:
        model = Subscription
        fields = (
            'name', 'slug', 'provider', 'type', 'status',
            'start_date', 'renewal_date', 'renewal_cost', 'currency',
            'billing_cycle', 'term_months', 'auto_renewal',
            'licensed_quantity', 'contract_reference', 'cost_center',
            'cancellation_date', 'owner', 'description', 'notes',
            'tags', 'tenant',
        )
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'provider': forms.Select(attrs={'class': 'form-select'}),
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
            'cost_center': forms.TextInput(attrs={'class': 'form-control'}),
            'cancellation_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'owner': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('subscriptions:subscription_list')

        self.helper.layout = Layout(
            Row(
                Column('name', css_class='col-md-6'),
                Column('slug', css_class='col-md-6'),
            ),
            Row(
                Column('provider', css_class='col-md-6'),
                Column('type', css_class='col-md-3'),
                Column('status', css_class='col-md-3'),
            ),
            Row(
                Column('start_date', css_class='col-md-4'),
                Column('renewal_date', css_class='col-md-4'),
                Column('term_months', css_class='col-md-4'),
            ),
            Row(
                Column('renewal_cost', css_class='col-md-3'),
                Column('currency', css_class='col-md-2'),
                Column('billing_cycle', css_class='col-md-3'),
                Column('auto_renewal', css_class='col-md-4'),
            ),
            Row(
                Column('licensed_quantity', css_class='col-md-4'),
                Column('contract_reference', css_class='col-md-4'),
                Column('cost_center', css_class='col-md-4'),
            ),
            Row(
                Column('cancellation_date', css_class='col-md-4'),
                Column('owner', css_class='col-md-4'),
                Column('tenant', css_class='col-md-4'),
            ),
            'description',
            'notes',
            'tags',
            HTML('<div class="mt-3 d-flex justify-content-between">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary">Cancel</a>'),
            HTML('</div>')
        )


class ProviderFilterForm(FilterForm):
    filterset_class = ProviderFilterSet


class SubscriptionFilterForm(FilterForm):
    filterset_class = SubscriptionFilterSet
