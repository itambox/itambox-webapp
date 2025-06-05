# assetbox/core/templatetags/core_constants.py
from django import template
from .. import constants

register = template.Library()


@register.simple_tag
def get_pagination_choices():
    """
    Return the PAGINATE_COUNT_CHOICES constant.
    """
    return constants.PAGINATE_COUNT_CHOICES 