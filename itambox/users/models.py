import hashlib
import hmac
import ipaddress
import secrets

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from core.models import ChangeLoggingMixin


def token_peppers():
    """Return the configured API-token peppers as ``{int id: secret}``.

    Peppers are server-side secrets combined (via HMAC-SHA256) with the random
    token before hashing, so a database leak alone does not yield usable tokens.
    Configured via ``ITAMBOX_API_TOKEN_PEPPERS`` (a JSON object of id->secret).
    When none are configured we fall back to a single pepper derived from
    SECRET_KEY so the app/tests work out of the box — production deployments
    should set an explicit, rotatable pepper.
    """
    configured = getattr(settings, 'API_TOKEN_PEPPERS', None)
    if configured:
        return {int(k): v for k, v in configured.items()}
    return {1: settings.SECRET_KEY}


def current_pepper_id():
    """The highest (newest) configured pepper id — used to hash new tokens."""
    return max(token_peppers())


def hash_token(plaintext, pepper_id):
    """HMAC-SHA256 digest of a plaintext token under the given pepper id."""
    pepper = token_peppers()[pepper_id]
    return hmac.new(
        pepper.encode('utf-8'), plaintext.encode('utf-8'), hashlib.sha256
    ).hexdigest()


def validate_cidr_list(value):
    """Validate that every entry in an allowed_ips list is a valid IPv4/IPv6 host or CIDR network."""
    for prefix in value:
        try:
            ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            raise ValidationError(
                _('"%(prefix)s" is not a valid IP address or CIDR prefix.'),
                params={'prefix': prefix},
            )


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


class Token(ChangeLoggingMixin, models.Model):
    """
    An API token used for authenticating REST API requests.

    The secret is never stored in plaintext: only an HMAC-SHA256 ``digest``
    (keyed by a server-side pepper) and a short non-secret ``key_preview`` for
    identification are persisted. The plaintext is generated once, shown once,
    and available on the in-memory instance via the ``key`` property until the
    object is reloaded from the database.
    """
    # HMAC-SHA256(pepper, plaintext) — the only at-rest representation of the
    # secret. `pepper` records which configured pepper produced this digest so
    # peppers can be rotated without rehashing.
    digest = models.CharField(max_length=64, unique=True, editable=False, blank=True)
    pepper = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    key_preview = models.CharField(max_length=16, blank=True, editable=False)
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
    allowed_ips = ArrayField(
        base_field=models.CharField(max_length=43),
        blank=True,
        default=list,
        validators=[validate_cidr_list],
        verbose_name=_('Allowed IPs'),
        help_text=_(
            'Permitted IPv4/IPv6 networks from which this token may be used, in CIDR notation '
            '(e.g. "192.168.1.0/24, 10.0.0.5"). Leave blank to allow any source address.'
        ),
    )

    # Transient plaintext, set on generation and cleared once the instance is
    # reloaded from the DB. Never persisted.
    _plaintext = None

    class Meta:
        ordering = ['-created']
        verbose_name = _("Token")
        verbose_name_plural = _("Tokens")

    def __str__(self):
        return f"{self.user.username}: {self.key_preview}..."

    @property
    def key(self):
        """The plaintext token — only available on the in-memory instance
        immediately after creation (shown once). Returns None once reloaded."""
        return self._plaintext

    @key.setter
    def key(self, value):
        self._plaintext = value

    def save(self, *args, **kwargs):
        if not self.digest:
            if not self._plaintext:
                self._plaintext = self.generate_key()
            self.pepper = current_pepper_id()
            self.digest = hash_token(self._plaintext, self.pepper)
            self.key_preview = self._plaintext[:8]
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

    @classmethod
    def find_by_key(cls, plaintext):
        """Resolve a token from a presented plaintext by comparing HMAC digests
        across all configured peppers (constant set, supports rotation)."""
        if not plaintext:
            return None
        digests = [hash_token(plaintext, pid) for pid in token_peppers()]
        return cls.objects.select_related('user').filter(digest__in=digests).first()

    @property
    def is_expired(self):
        if self.expires is None:
            return False
        return timezone.now() >= self.expires

    def validate_client_ip(self, client_ip):
        """
        Return True if the given client IP is permitted to use this token.

        An empty allowed_ips list imposes no restriction. An unparseable client
        IP, or one outside every configured prefix, is rejected (fail closed).
        """
        if not self.allowed_ips:
            return True
        try:
            client_addr = ipaddress.ip_address(client_ip)
        except (ValueError, TypeError):
            return False
        for prefix in self.allowed_ips:
            try:
                if client_addr in ipaddress.ip_network(prefix, strict=False):
                    return True
            except (ValueError, TypeError):
                continue
        return False
