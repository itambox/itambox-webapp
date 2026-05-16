from core.models import StandardModel

class PluginModel(StandardModel):
    """
    Base model for plugin data models.
    Plugins should inherit from this class to gain access to tenant scoping,
    change logging, tagging, journaling, and exporting features.
    """
    class Meta:
        abstract = True
