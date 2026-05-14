class PluginNavigationItem:
    """
    Base class for plugin sidebar navigation items.
    """
    link = None
    link_text = None
    permissions = ()
    auth_required = False
    staff_only = False
    buttons = ()


class PluginNavigationMenu:
    """
    Base class for plugin custom sidebar navigation menus.
    """
    label = None
    icon_class = 'mdi mdi-puzzle'
    groups = ()
