from django.conf import settings
from django.db import models

# Create your models here.

class UserPreference(models.Model):
    """
    Stores user-specific preferences, including table configurations.
    """
    THEME_DARK = 'dark'
    THEME_LIGHT = 'light'
    THEME_CHOICES = (
        (THEME_LIGHT, 'Light'),
        (THEME_DARK, 'Dark'),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='preferences' # Keep related_name for now, or change if needed
    )
    # Store various preferences as key-value pairs
    # Example: {"tables": {"assets.AssetTable": {"columns": [...]}}}
    data = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Preferences for {self.user.username}"

    class Meta:
        ordering = ('user',)
        # Set the app_label explicitly if moving models between apps after initial migration
        # This might not be strictly necessary if migrations handle it, but can prevent issues.
        # app_label = 'users' # Optional: uncomment if migrations cause issues
