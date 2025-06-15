from django.db import models
from django.db.models import Q, CheckConstraint
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, ChangeLoggingMixin
from extras.models import Tag
from software.models import Software
from assets.models import Asset
from organization.models import AssetHolder

class LicenseTypeChoices(models.TextChoices):
    PERPETUAL_SEAT = 'perpetual_seat', _('Perpetual Seat')
    SUBSCRIPTION_SEAT = 'subscription_seat', _('Subscription Seat')
    # Add others like 'Device', 'User CAL', 'Processor', 'Core' if needed later

class License(ChangeLoggingMixin, BaseModel):
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
        default=LicenseTypeChoices.PERPETUAL_SEAT
    )
    product_key = models.TextField(
        blank=True,
        help_text="Product key or activation code. Consider security implications."
    )
    seats = models.PositiveIntegerField(
        default=1,
        help_text="Total number of seats purchased or entitled"
    )
    purchase_date = models.DateField(blank=True, null=True)
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    order_number = models.CharField(max_length=100, blank=True)
    expiration_date = models.DateField(blank=True, null=True, help_text="For term licenses or maintenance")
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name='licenses')

    class Meta:
        ordering = ('software__manufacturer', 'software__name', 'name')
        verbose_name = "License"
        verbose_name_plural = "Licenses"

    def __str__(self):
        return f"{self.software.name} - {self.name} ({self.seats} seats)"

    def get_absolute_url(self):
        # return reverse('licenses:license_detail', kwargs={'pk': self.pk}) # Phase 2
        return "#" # Placeholder for now

    @property
    def available_seats(self):
        """Calculate the number of unassigned seats."""
        assigned_count = self.assignments.count()
        return max(0, self.seats - assigned_count)

class LicenseSeatAssignment(ChangeLoggingMixin, BaseModel):
    """Tracks the explicit assignment of one license seat to an asset or holder."""
    license = models.ForeignKey(
        to=License,
        on_delete=models.CASCADE,
        related_name='assignments'
    )
    asset = models.ForeignKey(
        to=Asset,
        on_delete=models.CASCADE,
        null=True, 
        blank=True,
        related_name='license_assignments'
    )
    assigned_holder = models.ForeignKey(
        to=AssetHolder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='license_assignments'
    )
    assigned_date = models.DateTimeField(auto_now_add=True, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('license', 'asset', 'assigned_holder')
        verbose_name = "License Seat Assignment"
        verbose_name_plural = "License Seat Assignments"
        constraints = [
            CheckConstraint(
                check=Q(asset__isnull=False, assigned_holder__isnull=True) | 
                      Q(asset__isnull=True, assigned_holder__isnull=False),
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
