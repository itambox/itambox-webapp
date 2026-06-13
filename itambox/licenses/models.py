from django.db import models
from django.db.models import Q, CheckConstraint
from django.urls import reverse, NoReverseMatch
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, ChangeLoggingMixin, DeletableVaultModel
from core.mixins import BookmarkableMixin, SoftDeleteMixin, CustomFieldDataMixin
from extras.models import Tag
from software.models import Software
from assets.models import Asset
from organization.models import AssetHolder
from core.crypto import encrypt_string, decrypt_string

class LicenseTypeChoices(models.TextChoices):
    PERPETUAL_SEAT = 'perpetual_seat', _('Perpetual Seat')
    SUBSCRIPTION_SEAT = 'subscription_seat', _('Subscription Seat')
    # Add others like 'Device', 'User CAL', 'Processor', 'Core' if needed later

from core.managers import SoftDeleteQuerySet, SoftDeleteManager, AllObjectsManager, TenantScopingSoftDeleteManager

from core.managers import TenantScopingSoftDeleteQuerySet

class LicenseQuerySet(TenantScopingSoftDeleteQuerySet):
    def with_counts(self):
        from django.db.models import Count
        # Only count *active* (non-soft-deleted) assignments. A bare
        # Count('assignments') joins the raw table and counts checked-in seats as
        # occupied, disagreeing with the related-manager path in available_seats.
        return self.annotate(
            assigned_count=Count(
                'assignments',
                filter=Q(assignments__deleted_at__isnull=True),
            )
        )


class SoftDeleteLicenseManager(TenantScopingSoftDeleteManager.from_queryset(LicenseQuerySet)):
    pass


class AllObjectsLicenseManager(AllObjectsManager.from_queryset(LicenseQuerySet)):
    pass


class License(CustomFieldDataMixin, BookmarkableMixin, DeletableVaultModel):
    """Represents the specific entitlement/purchase record for software."""

    name = models.CharField(
        max_length=255,
        help_text="Descriptive name for the license (e.g., Visio Pro 2021 - EA Renewal FY24)"
    )
    software = models.ForeignKey(
        to=Software,
        on_delete=models.PROTECT,
        related_name='licenses'
    )
    license_type = models.CharField(
        max_length=50,
        choices=LicenseTypeChoices.choices,
        default=LicenseTypeChoices.PERPETUAL_SEAT,
        db_index=True
    )
    product_key = models.TextField(
        blank=True,
        help_text="Product key or activation code. Consider security implications."
    )
    seats = models.PositiveIntegerField(
        default=1,
        help_text="Total number of seats purchased or entitled"
    )
    purchase_date = models.DateField(blank=True, null=True, db_index=True)
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    order_number = models.CharField(max_length=100, blank=True)
    expiration_date = models.DateField(blank=True, null=True, db_index=True, help_text="For term licenses or maintenance")
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name='licenses')
    supplier = models.ForeignKey('assets.Supplier', on_delete=models.SET_NULL, blank=True, null=True, related_name='licenses', db_index=True)
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='licenses', db_index=True)

    objects = SoftDeleteLicenseManager()
    all_objects = AllObjectsLicenseManager()

    class Meta:
        ordering = ('software__manufacturer', 'software__name', 'name')
        verbose_name = _("License")
        verbose_name_plural = _("Licenses")

    def __str__(self):
        return f"{self.software.name} - {self.name} ({self.seats} seats)"

    def get_absolute_url(self):
        try:
            return reverse('licenses:license_detail', kwargs={'pk': self.pk})
        except NoReverseMatch:
            # Fallback to Django admin change view if front-end view is not yet defined
            return reverse('admin:licenses_license_change', args=[self.pk])

    @property
    def decrypted_product_key(self):
        """Returns the decrypted product key value, handling plaintext fallback transparently."""
        return decrypt_string(self.product_key)

    def save(self, *args, **kwargs):
        """Symmetrically encrypt product key before saving to the database."""
        if self.product_key and not self.product_key.startswith("enc$"):
            self.product_key = encrypt_string(self.product_key)
        super().save(*args, **kwargs)

    @property
    def available_seats(self):
        """Calculate the number of unassigned seats, using annotation if available to avoid N+1 queries."""
        assigned_count = getattr(self, 'assigned_count', None)
        if assigned_count is None:
            assigned_count = self.assignments.count()
        return max(0, self.seats - assigned_count)

class LicenseSeatAssignment(SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    """Tracks the explicit assignment of one license seat to an asset or holder."""
    license = models.ForeignKey(
        to=License,
        on_delete=models.CASCADE,
        related_name='assignments',
        db_index=True
    )
    asset = models.ForeignKey(
        to=Asset,
        on_delete=models.SET_NULL,
        null=True, 
        blank=True,
        related_name='license_assignments',
        db_index=True
    )
    assigned_holder = models.ForeignKey(
        to=AssetHolder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='license_assignments',
        db_index=True
    )
    assigned_date = models.DateTimeField(auto_now_add=True, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('license', 'asset', 'assigned_holder')
        verbose_name = _("License Seat Assignment")
        verbose_name_plural = _("License Seat Assignments")
        constraints = [
            CheckConstraint(
                check=Q(asset__isnull=False, assigned_holder__isnull=True) | 
                      Q(asset__isnull=True, assigned_holder__isnull=False) |
                      Q(asset__isnull=True, assigned_holder__isnull=True),
                name='chk_assignment_to_one_target'
            )
        ]

    def __str__(self):
        target = self.asset or self.assigned_holder
        return f"Seat for {self.license} assigned to {target or 'Unspecified'}"

    def get_absolute_url(self):
        # Usually link back to the license itself
        return self.license.get_absolute_url()

    def clean(self):
        """Ensure assignment is to either asset or holder, not both or neither."""
        super().clean()
        if self.asset and self.assigned_holder:
            raise ValidationError(
                _("A license seat can only be assigned to an Asset OR an Asset Holder, not both."),
                code='invalid_assignment'
            )
        if not self.asset and not self.assigned_holder:
             raise ValidationError(
                 _("A license seat must be assigned to either an Asset or an Asset Holder."),
                 code='missing_assignment'
             )
