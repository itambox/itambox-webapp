from django import forms
from django.utils.translation import gettext_lazy as _
from assets.models import Manufacturer # Import Manufacturer
from extras.models import Tag
from core.forms import FilterForm, CrispyFormMixin, scope_tenant_field
from .filters import SoftwareFilterSet
from .models import Software, InstalledSoftware
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset, Div, Row, Column, HTML, Submit
from django.urls import reverse
from django.utils.html import format_html

# =============================================================================
# Software
# =============================================================================

from extras.customfields import CustomFieldModelFormMixin

class SoftwareForm(CrispyFormMixin, CustomFieldModelFormMixin, forms.ModelForm):
    """Form for creating and updating Software instances."""
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        label=_("Manufacturer"),
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )

    class Meta:
        model = Software
        fields = ('name', 'manufacturer', 'version', 'category', 'license_type', 'website', 'description', 'tenant', 'tags')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'version': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'license_type': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'website': forms.URLInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tenant': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }
        help_texts = {
            'name': _("Unique name of the software product (e.g., Microsoft Visio Professional 2021)"),
            'description': _("Optional description of the software product."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_tenant_field(self)
        self.fields['tenant'].required = False

        if self.instance and self.instance.pk:
            cancel_url = self.instance.get_absolute_url()
        else:
            cancel_url = reverse('software:software_list')

        self.helper.layout = Layout(
            Fieldset(
                _('Identity'),
                Div(
                    Div('name', css_class='col-md-8'),
                    Div('category', css_class='col-md-4'),
                    css_class='row',
                ),
                Div(
                    Div('manufacturer', css_class='col-md-6'),
                    Div('license_type', css_class='col-md-6'),
                    css_class='row',
                ),
                Div(
                    Div('version', css_class='col-md-4'),
                    Div('website', css_class='col-md-8'),
                    css_class='row',
                ),
                'description',
            ),
            Fieldset(
                _('Scope'),
                Div(
                    Div('tenant', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Notes & Tags'),
                'tags',
            ),
            *self.action_buttons(cancel_url),
        )
        self.append_custom_fields_to_layout()


class InstalledSoftwareForm(forms.ModelForm):
    """Record a software installation on an asset (asset-scoped quick-add)."""
    install_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )

    class Meta:
        model = InstalledSoftware
        fields = ['asset', 'software', 'version_detected', 'install_date', 'notes']
        widgets = {
            'asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'software': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'version_detected': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = _('Update') if self.instance and self.instance.pk else _('Install Software')
        try:
            cancel_url = reverse('software:software_list')
        except Exception:
            cancel_url = '/'

        self.helper.layout = Layout(
            'asset',
            'software',
            Row(
                Column('version_detected', css_class='col-md-6'),
                Column('install_date', css_class='col-md-6'),
            ),
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(format_html(
                '<a href="{}" class="btn btn-outline-secondary ms-2" data-no-dirty-track="true">Cancel</a>',
                cancel_url,
            )),
            HTML('</div>'),
        )


# --- Software Filter Form ---
class SoftwareFilterForm(FilterForm):
    filterset_class = SoftwareFilterSet
