from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Submit
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _


def add_standard_buttons(helper, instance, list_url_name):
    button_text = _("Update") if instance and instance.pk else _("Create")
    cancel_url = reverse(list_url_name)
    helper.layout.append(HTML('<div class="mt-4"></div>'))
    helper.layout.append(Submit("submit", button_text, css_class="btn btn-primary"))
    helper.layout.append(
        HTML(
            format_html(
                '<a href="{}" class="btn btn-outline-secondary ms-2" data-no-dirty-track="true">{}</a>',
                cancel_url,
                _("Cancel"),
            )
        )
    )
