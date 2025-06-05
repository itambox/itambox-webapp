from django.conf import settings
from django.db import models

class UserPreference(models.Model):
    """
    Stores user-specific preferences, including table configurations.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='preferences'
    )
    # Store various preferences as key-value pairs
    # Example: {"tables": {"assets.AssetTable": {"columns": [...]}}}
    data = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Preferences for {self.user.username}"

    class Meta:
        ordering = ('user',) 