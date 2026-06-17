"""AssetAssignment — checkout/checkin records linking assets to holders/locations."""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.models import BaseModel, ChangeLoggingMixin
from core.mixins import SoftDeleteMixin, JournalingMixin, TaggableMixin
from core.managers import TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager


class AssetAssignment(SoftDeleteMixin, JournalingMixin, TaggableMixin, ChangeLoggingMixin, BaseModel):
    # Tenant is derived from the parent asset; scope through it so assignments
    # cannot be listed or mutated across tenant boundaries.
    tenant_lookup = 'asset__tenant'
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    @property
    def tenant(self):
        return self.asset.tenant if self.asset_id else None

    asset = models.ForeignKey(
        'assets.Asset', on_delete=models.CASCADE, related_name='assignments', db_index=True
    )
    assigned_user = models.ForeignKey(
        'organization.AssetHolder', on_delete=models.SET_NULL, null=True, blank=True, related_name='asset_assignments'
    )
    assigned_location = models.ForeignKey(
        'organization.Location', on_delete=models.SET_NULL, null=True, blank=True, related_name='asset_assignments'
    )
    assigned_asset = models.ForeignKey(
        'assets.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='child_assignments'
    )
    pre_checkout_status = models.ForeignKey(
        'assets.StatusLabel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assignment_pre_checkouts',
        help_text="Preserved status label to revert to upon checkin."
    )

    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='checkouts'
    )
    checked_out_at = models.DateTimeField(default=timezone.now)
    expected_checkin_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='checkins'
    )
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='asset_assignments', blank=True)

    # Loaner-specific fields
    is_loan = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_('Is Loan'),
        help_text=_('Mark this assignment as a temporary loan with a mandatory return date.'),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Due Date'),
        help_text=_('Mandatory return date for loaner assets.'),
    )
    returned_at = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Returned At'),
        help_text=_('Date the loaned asset was physically returned.'),
    )

    class Meta:
        ordering = ['-checked_out_at']
        constraints = [
            models.UniqueConstraint(
                fields=['asset'],
                condition=models.Q(is_active=True),
                name='unique_active_assignment_per_asset'
            ),
            models.CheckConstraint(
                check=(
                    models.Q(assigned_user__isnull=False, assigned_location__isnull=True, assigned_asset__isnull=True) |
                    models.Q(assigned_user__isnull=True, assigned_location__isnull=False, assigned_asset__isnull=True) |
                    models.Q(assigned_user__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=False) |
                    models.Q(assigned_user__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=True)
                ),
                name='exactly_one_assignment_target'
            )
        ]
        verbose_name = _("Asset Assignment")
        verbose_name_plural = _("Asset Assignments")

    def clean(self):
        super().clean()
        targets = [self.assigned_user, self.assigned_location, self.assigned_asset]
        filled = [t for t in targets if t is not None]
        if self.is_active:
            if not filled:
                raise ValidationError(_("Either assigned_user, assigned_location, or assigned_asset must be provided for an active assignment."))
            if len(filled) > 1:
                raise ValidationError(_("You can only assign an asset to one target."))

            # Tenant boundary validation
            target = filled[0]
            if target and hasattr(target, 'tenant') and target.tenant != self.asset.tenant:
                raise ValidationError(_("Assignment target must belong to the same tenant as the asset."))

    @property
    def assigned_target(self):
        return self.assigned_user or self.assigned_location or self.assigned_asset

    @property
    def assigned_to(self):
        return self.assigned_target

    @property
    def assigned_to_type(self):
        if self.assigned_user: return 'assetholder'
        if self.assigned_location: return 'location'
        if self.assigned_asset: return 'asset'
        return None

    @property
    def is_overdue(self) -> bool:
        """True when this is an unreturned loan whose due date has passed."""
        if not self.is_loan:
            return False
        if self.returned_at:
            return False
        if not self.due_date:
            return False
        import datetime
        return datetime.date.today() > self.due_date

    def __str__(self):
        return f"{self.asset} → {self.assigned_target} ({'active' if self.is_active else 'inactive'})"

    def get_absolute_url(self):
        return self.asset.get_absolute_url()
