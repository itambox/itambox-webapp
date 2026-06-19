"""Asset state machine and the core Asset model."""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.models import DeletableVaultModel
from core.mixins import CustomFieldDataMixin, BookmarkableMixin, SubscribableMixin, SoftDeleteMixin
from core.managers import TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager
from core.currency import CurrencyField


class AssetStateMachine:
    ALLOWED_TRANSITIONS = {
        'pending': ['deployable', 'undeployable', 'archived', 'on_order', 'in_repair'],
        'deployable': ['pending', 'undeployable', 'archived', 'deployed', 'in_repair'],
        'deployed': ['deployable', 'undeployable', 'archived', 'pending', 'in_repair'],
        'undeployable': ['pending', 'deployable', 'archived', 'in_repair'],
        'archived': ['pending'],
        'in_repair': ['deployable', 'undeployable', 'archived'],
        # 'archived' is reachable from every non-terminal state: disposal is a
        # terminal action that may occur from any lifecycle point, including an
        # on-order asset cancelled/written off before it ever arrives.
        'on_order': ['pending', 'deployable', 'archived'],
    }

    @staticmethod
    def validate_transition(current_status_type, new_status_type, is_checked_out):
        if current_status_type == new_status_type:
            return
        if new_status_type not in AssetStateMachine.ALLOWED_TRANSITIONS.get(current_status_type, []):
            raise ValidationError(_("Illegal state transition from %(current)s to %(new)s") % {"current": current_status_type, "new": new_status_type})
        if is_checked_out and new_status_type in ['undeployable', 'archived']:
            raise ValidationError(_("Cannot mark an actively checked-out asset as undeployable or archived. Check it in first."))


class Asset(CustomFieldDataMixin, BookmarkableMixin, SubscribableMixin, DeletableVaultModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    # NOTE: asset status is a FK to StatusLabel; the lifecycle vocabulary is
    # StatusLabel.type (assets.choices.StatusTypeChoices), not a local choice set.

    name = models.CharField(max_length=255, verbose_name=_("Name"))
    asset_tag = models.CharField(max_length=50, blank=True, verbose_name=_("Asset Tag"))
    serial_number = models.CharField(max_length=100, blank=True, db_index=True, verbose_name=_("Serial Number"))
    asset_type = models.ForeignKey('assets.AssetType', on_delete=models.PROTECT, related_name='assets', null=True, blank=True, db_index=True, verbose_name=_("Asset Type"))
    asset_role = models.ForeignKey('assets.AssetRole', on_delete=models.SET_NULL, blank=True, null=True, related_name='assets', db_index=True, verbose_name=_("Asset Role"))
    purchase_date = models.DateField(blank=True, null=True, db_index=True, verbose_name=_("Purchase Date"))

    # Procurement Metadata (Maturity Phase 1)
    purchase_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Purchase Cost")
    )
    current_book_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Current Book Value"),
        help_text=_("Materialized depreciation value")
    )
    depreciation_updated_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Depreciation Updated At"))
    order_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Order Number")
    )
    supplier = models.ForeignKey(
        'assets.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        verbose_name=_("Supplier"),
        db_index=True
    )
    salvage_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Salvage Value")
    )
    currency = CurrencyField()
    status = models.ForeignKey('assets.StatusLabel', on_delete=models.PROTECT, related_name='assets', null=True, blank=True, db_index=True, verbose_name=_("Status"))
    location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, blank=True, null=True, related_name='assets', db_index=True, verbose_name=_("Location"))
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='assets', db_index=True, verbose_name=_("Tenant"))
    purchase_order_line = models.ForeignKey('procurement.PurchaseOrderLine', on_delete=models.SET_NULL, blank=True, null=True, related_name='assets', db_index=True, verbose_name=_("Purchase Order Line"))
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    tags = models.ManyToManyField('extras.Tag', related_name="assets", blank=True, verbose_name=_("Tags"))
    last_audited = models.DateTimeField(null=True, blank=True, verbose_name=_("Last Audited"), db_index=True)
    last_audited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audited_assets',
        verbose_name=_("Last Audited By")
    )
    # custom_field_data JSONField comes from CustomFieldDataMixin
    requestable = models.BooleanField(null=True, blank=True, default=None, db_index=True, verbose_name=_("Requestable"), help_text=_("Allow users to request this asset"))
    depreciation_override = models.ForeignKey(
        'assets.Depreciation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_overrides',
        verbose_name=_("Depreciation override"),
        help_text=_("Override depreciation policy — leave empty to use the tenant default or asset-type schedule."),
    )
    in_service_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("In-service date"),
        help_text=_("Depreciation starts here; falls back to purchase date."),
    )
    cost_center = models.ForeignKey(
        'organization.CostCenter',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        verbose_name=_('Cost Center'),
        db_index=True,
    )
    disposed_at = models.DateTimeField(null=True, blank=True, editable=False, verbose_name=_("Disposed at"))
    disposal_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        verbose_name=_("Sign-off value"),
    )

    @property
    def current_warranty_end(self):
        """Max end_date among all currently active warranties, or None."""
        import datetime
        today = datetime.date.today()
        result = (
            self.warranties
            .filter(start_date__lte=today, end_date__gte=today, deleted_at__isnull=True)
            .order_by('-end_date')
            .values_list('end_date', flat=True)
            .first()
        )
        return result

    @property
    def is_requestable(self):
        if self.requestable is not None:
            return self.requestable
        return self.asset_type.requestable if self.asset_type else False

    @property
    def manufacturer(self):
        return self.asset_type.manufacturer if self.asset_type else None

    @property
    def model(self):
        return self.asset_type.model if self.asset_type else None

    @property
    def audit_due_date(self):
        """Date by which the next physical audit is due, or None if no cadence is set.

        Never-audited assets with a cadence are overdue immediately (returns created_at).
        """
        category = self.category
        if not category or not category.audit_interval_months:
            return None
        from datetime import timedelta
        interval_days = category.audit_interval_months * 30
        base = self.last_audited or self.created_at
        return base + timedelta(days=interval_days)

    @property
    def audit_overdue(self) -> bool:
        """True when a cadence is set and the due date has passed."""
        from django.utils import timezone
        due = self.audit_due_date
        return due is not None and timezone.now() > due

    def get_status_display(self):
        return self.status.name if self.status else "—"

    @property
    def eol_date(self):
        if self.purchase_date and self.asset_type and self.asset_type.eol_months:
            from dateutil.relativedelta import relativedelta
            # relativedelta clamps month-end overflow (Jan 31 + 1 month = Feb 28/29).
            return self.purchase_date + relativedelta(months=self.asset_type.eol_months)
        return None

    @property
    def time_to_eol(self):
        eol = self.eol_date
        if eol:
            import datetime
            from dateutil.relativedelta import relativedelta
            today = datetime.date.today()
            if today >= eol:
                return "Expired"

            delta = relativedelta(eol, today)
            parts = []
            if delta.years > 0:
                parts.append(f"{delta.years} year{'s' if delta.years != 1 else ''}")
            if delta.months > 0:
                parts.append(f"{delta.months} month{'s' if delta.months != 1 else ''}")
            return ", ".join(parts) or "Less than a month"
        return "—"

    @property
    def total_cost_of_ownership(self):
        from decimal import Decimal
        cost = self.purchase_cost or Decimal('0.00')
        maintenance_cost = sum(m.cost or Decimal('0.00') for m in self.maintenances.all())
        return cost + maintenance_cost

    @property
    def current_value(self):
        """Estimated book value — delegates to the pure compute_book_value function."""
        from assets.depreciation import compute_book_value
        return compute_book_value(self)

    @property
    def is_modular(self):
        if self.component_allocations.filter(deleted_at__isnull=True).exists():
            return True
        return bool(self.asset_role and self.asset_role.allows_components)

    @property
    def active_assignment(self):
        prefetched = getattr(self, 'prefetched_active_assignments', None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return self.assignments.filter(is_active=True).first()

    @property
    def assigned_to(self):
        active = self.active_assignment
        return active.assigned_target if active else None

    @property
    def category(self):
        return self.asset_type.category if self.asset_type else None

    class Meta:
        verbose_name = _("Asset")
        verbose_name_plural = _("Assets")
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'asset_tag'],
                condition=models.Q(tenant__isnull=False),
                name='unique_tenant_asset_tag'
            ),
            models.UniqueConstraint(
                fields=['asset_tag'],
                condition=models.Q(tenant__isnull=True),
                name='unique_global_asset_tag'
            ),
            models.UniqueConstraint(
                fields=['tenant', 'serial_number'],
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(serial_number=''),
                name='uniq_active_tenant_serial'
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.asset_tag})"

    def get_absolute_url(self):
        """Return the canonical URL for the asset."""
        return reverse('assets:asset_detail', kwargs={'pk': self.pk})

    def clean(self):
        super().clean()
        if self.pk and self.status_id:
            # Integrity checks must see the row as stored, not through the current
            # request's tenant/soft-delete lens — otherwise a context mismatch
            # (background task, cross-tenant admin) silently skips the state machine.
            old_asset = Asset._base_manager.filter(pk=self.pk).first()
            if old_asset and old_asset.status_id and old_asset.status != self.status:
                AssetStateMachine.validate_transition(
                    old_asset.status.type,
                    self.status.type,
                    self.assignments.filter(is_active=True).exists()
                )

    def save(self, *args, **kwargs):
        from assets.models.tagsequence import AssetTagSequence
        if not self.asset_tag:
            self.asset_tag = AssetTagSequence.get_next_tag_for_asset(self)
        else:
            seq = AssetTagSequence.resolve_sequence_for_asset(self)
            if seq and self.asset_tag == seq.next_tag_preview:
                seq.next_tag()

        # Freeze/unfreeze sign-off value on archive transition.
        # State-machine transition validation is NOT done here: it lives in
        # clean(), which the global `validate_custom_validators_on_save` pre_save
        # signal (core/signals.py) runs on every ChangeLoggingMixin save — so any
        # status change via save() is validated exactly once, before mutation.
        if self.pk:
            old = Asset._base_manager.filter(pk=self.pk).select_related('status').first()
            if old:
                old_type = old.status.type if old.status else None
                new_type = self.status.type if self.status else None
                if old_type != 'archived' and new_type == 'archived':
                    from assets.depreciation import compute_book_value
                    from decimal import Decimal
                    self.disposal_value = compute_book_value(self) or Decimal('0.00')
                    self.disposed_at = timezone.now()
                elif old_type == 'archived' and new_type != 'archived':
                    self.disposed_at = None
                    self.disposal_value = None

        super().save(*args, **kwargs)
