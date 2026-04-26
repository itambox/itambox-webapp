from django import template
from itambox import constants

register = template.Library()


@register.simple_tag
def get_pagination_choices():
    return constants.PAGINATE_COUNT_CHOICES
