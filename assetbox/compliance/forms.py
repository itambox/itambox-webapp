from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column
from core.forms import FilterForm
from assets.models import Asset, Supplier
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
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False,
        label="Supplier"
    )
    maintenance_type = forms.ChoiceField(
        choices=AssetMaintenance.MAINTENANCE_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Maintenance Type"
    )
    status = forms.ChoiceField(
        choices=AssetMaintenance._meta.get_field('status').choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Status"
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
            'asset', 'title', 'supplier', 'maintenance_type', 'status',
            'cost', 'start_date', 'completion_date', 'performed_by',
            'description', 'notes'
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'performed_by': forms.TextInput(attrs={'class': 'form-control'}),
            'cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('compliance:assetmaintenance_list')

        self.helper.layout = Layout(
            Row(
                Column('asset', css_class='col-md-6'),
                Column('title', css_class='col-md-6')
            ),
            Row(
                Column('supplier', css_class='col-md-6'),
                Column('performed_by', css_class='col-md-6')
            ),
            Row(
                Column('maintenance_type', css_class='col-md-6'),
                Column('status', css_class='col-md-6')
            ),
            Row(
                Column('cost', css_class='col-md-6'),
            ),
            Row(
                Column('start_date', css_class='col-md-6'),
                Column('completion_date', css_class='col-md-6')
            ),
            'description',
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )
