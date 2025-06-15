from django import forms
from django.utils.translation import gettext_lazy as _
from assets.models import Manufacturer # Import Manufacturer
from extras.models import Tag
from core.forms import BootstrapMixin, FilterForm # Assuming a BootstrapMixin exists in core
from .filters import SoftwareFilterSet
from .models import Software
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div
from django.urls import reverse

# =============================================================================
# Software
# =============================================================================

class SoftwareForm(BootstrapMixin, forms.ModelForm):
    """Form for creating and updating Software instances."""
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        label=_("Manufacturer")
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )

    class Meta:
        model = Software
        fields = ('name', 'manufacturer', 'description', 'tags')
        help_texts = {
            'name': _("Unique name of the software product (e.g., Microsoft Visio Professional 2021)"),
            'description': _("Optional description of the software product."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        if self.instance and self.instance.pk:
            button_text = _('Update')
            cancel_url = self.instance.get_absolute_url()
        else:
            button_text = _('Create')
            cancel_url = reverse('software:software_list')

        self.helper.layout = Layout(
            Div(
                'name',
                'manufacturer',
                'description',
                'tags',
                css_class='mb-3'
            ),
            HTML('<div class="mt-3 d-flex justify-content-between">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary">{_("Cancel")}</a>'),
            HTML('</div>')
        )


# --- Software Filter Form --- 
class SoftwareFilterForm(FilterForm):
    filterset_class = SoftwareFilterSet 