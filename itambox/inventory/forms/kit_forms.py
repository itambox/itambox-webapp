from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

from core.forms import FilterForm
from extras.models import Tag
from organization.models import Location
from ..models import Kit, KitItem
from ..filters import KitFilterSet
from .base_forms import BaseCheckoutForm


class KitForm(forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
    )

    class Meta:
        model = Kit
        fields = ['name', 'description', 'tenant', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('inventory:kit_list')

        self.helper.layout = Layout(
            'name',
            'description',
            'tenant',
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class KitItemForm(forms.ModelForm):
    class Meta:
        model = KitItem
        fields = ['kit', 'asset_type', 'accessory', 'license', 'consumable', 'qty']
        widgets = {
            'kit': forms.Select(attrs={'class': 'form-select'}),
            'asset_type': forms.Select(attrs={'class': 'form-select'}),
            'accessory': forms.Select(attrs={'class': 'form-select'}),
            'license': forms.Select(attrs={'class': 'form-select'}),
            'consumable': forms.Select(attrs={'class': 'form-select'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.kit.get_absolute_url() if (self.instance.pk and self.instance.kit) else reverse('inventory:kit_list')

        self.helper.layout = Layout(
            'kit',
            'asset_type',
            'accessory',
            'license',
            'consumable',
            'qty',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

    def clean(self):
        cleaned_data = super().clean()
        asset_type = cleaned_data.get('asset_type')
        accessory = cleaned_data.get('accessory')
        license_item = cleaned_data.get('license')
        consumable = cleaned_data.get('consumable')

        targets = [asset_type, accessory, license_item, consumable]
        filled = [t for t in targets if t is not None]
        if len(filled) == 0:
            raise ValidationError("A kit item must select either an Asset Type, Accessory, License, or Consumable.")
        if len(filled) > 1:
            raise ValidationError("A kit item cannot select more than one target (must be either Asset Type OR Accessory OR License OR Consumable).")
        return cleaned_data


class KitCheckoutForm(BaseCheckoutForm):
    source_location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Source Location (stock deduction)"
    )

    def __init__(self, *args, **kwargs):
        self.kit = kwargs.pop('kit', None)
        tenant = self.kit.tenant if self.kit else None
        super().__init__(*args, tenant=tenant, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'source_location',
            'assigned_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'assigned_location',
            'notes'
        )


class KitFilterForm(FilterForm):
    filterset_class = KitFilterSet
