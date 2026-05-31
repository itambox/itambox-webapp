from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
import secrets


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
        verbose_name = _("User Preference")
        verbose_name_plural = _("User Preferences")


class Token(models.Model):
    """
    An API token used for authenticating REST API requests.
    """
    key = models.CharField(max_length=40, unique=True, db_index=True)
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tokens'
    )
    tenant = models.ForeignKey(
        to='organization.Tenant',
        on_delete=models.CASCADE,
        related_name='tokens',
        db_index=True
    )
    created = models.DateTimeField(auto_now_add=True)
    expires = models.DateTimeField(blank=True, null=True, db_index=True)
    last_used = models.DateTimeField(blank=True, null=True)
    write_enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created']
        verbose_name = _("Token")
        verbose_name_plural = _("Tokens")

    def __str__(self):
        return f"{self.user.username}: {self.key[:6]}..."

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        if not getattr(self, 'tenant_id', None):
            from core.managers import get_current_tenant
            tenant = get_current_tenant()
            if not tenant:
                from organization.models import Tenant
                tenant = Tenant._base_manager.first()
                if not tenant:
                    tenant = Tenant._base_manager.create(
                        name="Default Tenant",
                        slug="default-tenant"
                    )
            self.tenant = tenant
        super().save(*args, **kwargs)

    @staticmethod
    def generate_key():
        return secrets.token_hex(20)

    @property
    def is_expired(self):
        if self.expires is None:
            return False
        return timezone.now() >= self.expires
