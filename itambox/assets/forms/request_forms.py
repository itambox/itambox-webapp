from django import forms
from django.core.exceptions import ValidationError

from assets.models import Asset, AssetType, AssetRequest


class AssetRequestForm(forms.ModelForm):
    TARGET_CHOICES = [
        ('', 'Myself'),
        ('assetholder', 'Asset Holder'),
        ('location', 'Location'),
        ('asset', 'Asset'),
    ]

    target_type = forms.ChoiceField(
        choices=TARGET_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Request For"
    )

    class Meta:
        model = AssetRequest
        fields = ['asset_type', 'asset', 'target_type', 'assigned_user', 'assigned_location', 'assigned_asset', 'notes']
        widgets = {
            'asset_type': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'assigned_user': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'assigned_location': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'assigned_asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        from django.db.models import Q
        from core.managers import get_current_tenant
        from crispy_forms.helper import FormHelper
        from crispy_forms.layout import Layout, Submit, HTML
        from organization.models import Location, AssetHolder

        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        user = self.request.user if self.request else None
        can_delegate = user.has_perm('assets.add_delegated_assetrequest') if user else False

        tenant = get_current_tenant()

        # Only allow requestable objects
        self.fields['asset_type'].queryset = AssetType.objects.filter(requestable=True)
        # Only allow requestable and deployable assets
        self.fields['asset'].queryset = Asset.objects.filter(
            Q(requestable=True) | Q(requestable__isnull=True, asset_type__requestable=True),
            status__type='deployable'
        )

        if tenant:
            self.fields['asset'].queryset = self.fields['asset'].queryset.filter(tenant=tenant)
            self.fields['assigned_user'].queryset = AssetHolder.objects.filter(tenant=tenant).order_by('last_name', 'first_name')
            self.fields['assigned_location'].queryset = Location.objects.filter(tenant=tenant).select_related('site').order_by('site__name', 'name')
            self.fields['assigned_asset'].queryset = Asset.objects.filter(tenant=tenant).order_by('name')
        else:
            self.fields['assigned_user'].queryset = AssetHolder.objects.all().order_by('last_name', 'first_name')
            self.fields['assigned_location'].queryset = Location.objects.all().select_related('site').order_by('site__name', 'name')
            self.fields['assigned_asset'].queryset = Asset.objects.all().order_by('name')

        # Set initial value for target_type if instance exists
        if self.instance and self.instance.pk:
            if self.instance.assigned_user:
                self.fields['target_type'].initial = 'assetholder'
            elif self.instance.assigned_location:
                self.fields['target_type'].initial = 'location'
            elif self.instance.assigned_asset:
                self.fields['target_type'].initial = 'asset'
            else:
                self.fields['target_type'].initial = ''

        if not can_delegate:
            self.fields['target_type'].choices = [('', 'Myself')]
            self.fields['target_type'].widget = forms.HiddenInput()
            self.fields['assigned_user'].widget = forms.HiddenInput()
            self.fields['assigned_location'].widget = forms.HiddenInput()
            self.fields['assigned_asset'].widget = forms.HiddenInput()

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        cancel_url = '#'
        if self.instance and self.instance.pk:
            cancel_url = self.instance.get_absolute_url()
        else:
            from django.urls import reverse
            cancel_url = reverse('assets:assetrequest_list')

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        
        self.helper.layout = Layout(
            'asset_type',
            'asset',
            'target_type',
            'assigned_user',
            'assigned_location',
            'assigned_asset',
            'notes',
            HTML('<div class="mt-4"></div>'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>')
        )

    def clean(self):
        cleaned_data = super().clean()
        asset_type = cleaned_data.get('asset_type')
        asset = cleaned_data.get('asset')
        target_type = cleaned_data.get('target_type')
        assigned_user = cleaned_data.get('assigned_user')
        assigned_location = cleaned_data.get('assigned_location')
        assigned_asset = cleaned_data.get('assigned_asset')

        user = self.request.user if self.request else None
        can_delegate = user.has_perm('assets.add_delegated_assetrequest') if user else False

        if not asset_type and not asset:
            raise ValidationError("Either Asset Type or specific Asset must be selected.")
        if asset and asset_type and asset.asset_type != asset_type:
            raise ValidationError({"asset": "Selected asset does not match the chosen asset type."})

        # Process target selections based on target_type
        if target_type == 'assetholder':
            if not can_delegate:
                raise ValidationError("You do not have permission to request assets on behalf of others.")
            if not assigned_user:
                raise ValidationError({"assigned_user": "Please select an Asset Holder target."})
            cleaned_data['assigned_location'] = None
            cleaned_data['assigned_asset'] = None
        elif target_type == 'location':
            if not can_delegate:
                raise ValidationError("You do not have permission to request assets on behalf of others.")
            if not assigned_location:
                raise ValidationError({"assigned_location": "Please select a Location target."})
            cleaned_data['assigned_user'] = None
            cleaned_data['assigned_asset'] = None
        elif target_type == 'asset':
            if not can_delegate:
                raise ValidationError("You do not have permission to request assets on behalf of others.")
            if not assigned_asset:
                raise ValidationError({"assigned_asset": "Please select a Parent Asset target."})
            cleaned_data['assigned_user'] = None
            cleaned_data['assigned_location'] = None
        else: # target_type == '' (Myself)
            cleaned_data['assigned_user'] = None
            cleaned_data['assigned_location'] = None
            cleaned_data['assigned_asset'] = None

        return cleaned_data


class AssetRequestActionForm(forms.Form):
    allocated_asset = forms.ModelChoiceField(
        queryset=Asset.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label="Allocate Specific Asset"
    )
    response_notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=False,
        label="Response/Decision Notes"
    )

    def __init__(self, *args, **kwargs):
        from django.db.models import Q
        request_instance = kwargs.pop('request_instance', None)
        super().__init__(*args, **kwargs)
        if request_instance:
            target_type = request_instance.asset_type
            if target_type:
                self.fields['allocated_asset'].queryset = Asset.objects.filter(
                    Q(requestable=True) | Q(requestable__isnull=True, asset_type__requestable=True),
                    asset_type=target_type,
                    status__type='deployable'
                )
            else:
                self.fields['allocated_asset'].queryset = Asset.objects.filter(
                    Q(requestable=True) | Q(requestable__isnull=True, asset_type__requestable=True),
                    status__type='deployable'
                )


class AssetRequestResponseForm(forms.ModelForm):
    class Meta:
        model = AssetRequest
        fields = ['status', 'response_notes']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'response_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

