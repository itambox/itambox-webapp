from django import forms
from django.utils.translation import gettext_lazy as _
from software.models import Software
from extras.models import Tag
from core.forms import BootstrapMixin, FilterForm
from .filters import LicenseFilterSet
from .models import License, LicenseSeatAssignment
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div
from django.urls import reverse

class LicenseForm(BootstrapMixin, forms.ModelForm):
    """Form for creating and updating License entitlements."""
    software = forms.ModelChoiceField(
        queryset=Software.objects.all(),
        label=_("Software Catalog Item")
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )

    class Meta:
        model = License
        fields = ('name', 'software', 'license_type', 'product_key', 'seats', 'purchase_date', 'purchase_cost', 'order_number', 'expiration_date', 'notes', 'tags', 'tenant')
        widgets = {
            'product_key': forms.Textarea(attrs={'rows': 2}),
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'expiration_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'name': _("Unique renewal or purchase name (e.g., Office 365 E5 Enterprise Renewal FY26)"),
            'seats': _("Total number of activation seats purchased"),
            'product_key': _("License activation key or volume credential"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['product_key'].initial = self.instance.decrypted_product_key
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        if self.instance and self.instance.pk:
            button_text = _('Update')
            cancel_url = self.instance.get_absolute_url()
        else:
            button_text = _('Create')
            cancel_url = reverse('licenses:license_list')

        self.helper.layout = Layout(
            Div(
                'name',
                'software',
                'license_type',
                'product_key',
                'seats',
                'purchase_date',
                'purchase_cost',
                'order_number',
                'expiration_date',
                'tenant',
                'notes',
                'tags',
                css_class='mb-3'
            ),
            HTML('<div class="mt-3 d-flex justify-content-between">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary">{_("Cancel")}</a>'),
            HTML('</div>')
        )

class LicenseFilterForm(FilterForm):
    filterset_class = LicenseFilterSet
