class PluginTemplateContent:
    """
    Base class for plugin template content injections.
    Plugins subclass this to inject custom HTML content into core templates.
    """
    def __init__(self, context):
        self.context = context

    def buttons(self):
        """Inject content into the action buttons area."""
        return ''

    def left_panel(self):
        """Inject content into the left panel of the detail page."""
        return ''

    def right_panel(self):
        """Inject content into the right panel of the detail page."""
        return ''

    def full_width_panel(self):
        """Inject content into the full width panel at the bottom of the detail page."""
        return ''
