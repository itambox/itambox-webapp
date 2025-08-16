from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column
from core.forms import FilterForm
from assets.models import Asset
from .models import AssetMaintenance

class AssetMaintenanceFilterForm(FilterForm):
    from .filters import AssetMaintenanceFilterSet
    filterset_class = AssetMaintenanceFilterSet

class AssetMaintenanceForm(forms.ModelForm):
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Asset"
    )
    maintenance_type = forms.ChoiceField(
        choices=AssetMaintenance.MAINTENANCE_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Maintenance Type"
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label="Start Date"
    )
    completion_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=False,
        label="Completion Date"
    )

    class Meta:
        model = AssetMaintenance
        fields = [
            'asset', 'supplier', 'maintenance_type', 'cost',
            'start_date', 'completion_date', 'notes'
        ]
        widgets = {
            'supplier': forms.TextInput(attrs={'class': 'form-control'}),
            'cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('assets:assetmaintenance_list')

        self.helper.layout = Layout(
            Row(
                Column('asset', css_class='col-md-6'),
                Column('supplier', css_class='col-md-6')
            ),
            Row(
                Column('maintenance_type', css_class='col-md-6'),
                Column('cost', css_class='col-md-6')
            ),
            Row(
                Column('start_date', css_class='col-md-6'),
                Column('completion_date', css_class='col-md-6')
            ),
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )
