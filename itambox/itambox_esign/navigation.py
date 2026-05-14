from itambox.plugins.navigation import PluginNavigationMenu, PluginNavigationItem

class EsignNavigationItem(PluginNavigationItem):
    link = 'dashboard'
    link_text = 'DocuSign Envelopes'

class EsignGroup:
    label = 'E-Signature'
    items = (EsignNavigationItem,)

class EsignNavigationMenu(PluginNavigationMenu):
    label = 'DocuSign'
    icon_class = 'mdi mdi-file-sign'
    groups = (EsignGroup,)
