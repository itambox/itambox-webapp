from django import template

from core.navigation.menu import get_menus

register = template.Library()


@register.inclusion_tag("navigation/menu.html", takes_context=True)
def nav(context):
    user = context['request'].user
    nav_items = []

    for menu in get_menus():
        groups = []
        for group in menu.groups:
            items = []
            for item in group.items:
                if getattr(item, 'auth_required', False) and not user.is_authenticated:
                    continue
                if not user.has_perms(item.permissions):
                    continue
                if item.staff_only and not user.is_staff:
                    continue
                buttons = [
                    button for button in item.buttons
                    if user.has_perms(button.permissions)
                ]
                items.append((item, buttons))
            if items:
                groups.append((group, items))
        if groups:
            nav_items.append((menu, groups))

    return {'nav_items': nav_items}
