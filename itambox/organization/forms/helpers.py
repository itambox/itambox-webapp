from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, HTML


def add_standard_buttons(helper, instance, list_url_name):
    button_text = 'Update' if instance and instance.pk else 'Create'
    cancel_url = reverse(list_url_name)
    helper.layout.append(
        HTML('<div class="mt-4"></div>')
    )
    helper.layout.append(
        Submit('submit', button_text, css_class='btn btn-primary')
    )
    helper.layout.append(
        HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>')
    )
