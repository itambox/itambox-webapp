import datetime
from django import forms
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, HTML

from organization.models import Location, AssetHolder
from assets.models import Asset, StatusLabel


class AssetCheckOutForm(forms.Form):
    TARGET_CHOICES = [
        ('holder', 'Asset Holder'),
        ('location', 'Location'),
        ('asset', 'Asset'),
    ]

    target_type = forms.ChoiceField(
        choices=TARGET_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to"
    )
    asset_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Asset Holder"
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Location"
    )
    asset_target = forms.ModelChoiceField(
        queryset=Asset.objects.exclude(
            status__type__in=['undeployable', 'in_repair', 'on_order', 'archived']
        ).order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Parent Asset"
    )
    status = forms.ModelChoiceField(
        queryset=StatusLabel.objects.filter(type='deployed').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Status"
    )
    checkout_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label="Checkout Date"
    )
    expected_checkin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label="Expected Checkin Date"
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=False,
        label="Notes"
    )
 
    def clean(self):
        cleaned_data = super().clean()
        target_type = cleaned_data.get('target_type')
        holder = cleaned_data.get('asset_holder')
        location = cleaned_data.get('location')
        asset_target = cleaned_data.get('asset_target')
 
        if target_type == 'holder' and not holder:
            raise ValidationError("Must select an Asset Holder.", code='holder_required')
        if target_type == 'location' and not location:
            raise ValidationError("Must select a Location.", code='location_required')
        if target_type == 'asset' and not asset_target:
            raise ValidationError("Must select an Asset.", code='asset_required')
        if not target_type:
            raise ValidationError("Must select a target type.", code='target_type_required')
        return cleaned_data
 
    def __init__(self, *args, **kwargs):
        asset = kwargs.pop('asset', None)
        super().__init__(*args, **kwargs)
        if asset:
            self.fields['asset_target'].queryset = Asset.objects.exclude(pk=asset.pk).exclude(
                status__type__in=['undeployable', 'in_repair', 'on_order', 'archived']
            ).order_by('name')
            if asset.tenant:
                self.fields['asset_holder'].queryset = AssetHolder.objects.filter(tenant=asset.tenant).order_by('last_name', 'first_name')
                self.fields['location'].queryset = Location.objects.filter(tenant=asset.tenant).select_related('site').order_by('site__name', 'name')
        
        initial_status = StatusLabel.objects.filter(type='deployed').first()
        if initial_status:
            self.fields['status'].initial = initial_status

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'target_type',
            'asset_holder',
            'location',
            'asset_target',
            'status',
            'checkout_date',
            'expected_checkin',
            'notes',
        )


class AssetCheckInForm(forms.Form):
    status = forms.ModelChoiceField(
        queryset=StatusLabel.objects.exclude(type='deployed').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Status"
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Location"
    )
    checkin_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label="Checkin Date"
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=False,
        label="Notes"
    )

    def __init__(self, *args, **kwargs):
        asset = kwargs.pop('asset', None)
        super().__init__(*args, **kwargs)
        
        initial_status = None
        if asset:
            active_assignment = asset.active_assignment
            if active_assignment:
                initial_status = active_assignment.pre_checkout_status
            if not initial_status:
                initial_status = StatusLabel.objects.filter(type='deployable').first()
            if asset.tenant:
                self.fields['location'].queryset = Location.objects.filter(tenant=asset.tenant).select_related('site').order_by('site__name', 'name')
        else:
            initial_status = StatusLabel.objects.filter(type='deployable').first()
            
        if initial_status:
            self.fields['status'].initial = initial_status

        self.fields['checkin_date'].initial = datetime.date.today()

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'status',
            'location',
            'checkin_date',
            'notes',
        )

from inventory.forms import BaseCheckoutForm, AccessoryCheckoutForm, ConsumableCheckoutForm, KitCheckoutForm
