import secrets
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from core.models import BaseModel, ChangeLoggingMixin, StandardModel
from core.mixins import TaggableMixin, CloneableMixin, ExportableMixin, JournalingMixin, ImageAttachmentMixin, FileAttachmentMixin, SoftDeleteMixin
from core.managers import SoftDeleteManager, AllObjectsManager, TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager
from compliance.choices import AuditSessionStatusChoices, AuditVerificationMethodChoices


def generate_token():
    return secrets.token_urlsafe(48)


class CustodyTemplate(TaggableMixin, CloneableMixin, ExportableMixin, ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    tenant = models.ForeignKey(
        to='organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='custody_templates',
        verbose_name=_("Tenant")
    )
    tenant_group = models.ForeignKey(
        to='organization.TenantGroup',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='custody_templates',
        verbose_name=_("Tenant Group"),
        help_text=_("Target Tenant Group this template applies to (conglomerate/divisional scope).")
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("Name"),
        help_text=_("Template Name (e.g. Standard Laptop EULA)")
    )
    signature_provider = models.CharField(
        max_length=50,
        default='local',
        verbose_name=_("Signature Provider"),
        help_text=_("E-Signature workflow provider module")
    )
    logo = models.ImageField(
        upload_to='custody_logos/',
        blank=True,
        null=True,
        verbose_name=_("Logo"),
        help_text=_("Custom EULA / signoff logo image")
    )
    eula_text = models.TextField(
        verbose_name=_("EULA Text"),
        help_text=_("Terms of Service / EULA guidelines")
    )
    disclaimer = models.TextField(
        blank=True,
        verbose_name=_("Disclaimer"),
        help_text=_("Disclaimer statement printed at signoff")
    )
    qms_reference = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("QMS Reference"),
        help_text=_("Quality Management System document reference key")
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Is Active"),
        help_text=_("Deactivate to hide from choices")
    )
    require_acceptance = models.BooleanField(
        default=True,
        verbose_name=_("Require Acceptance"),
        help_text=_("Require digital signature / EULA acceptance on checkout.")
    )
    email_signature_request = models.BooleanField(
        default=True,
        verbose_name=_("Email Signature Request"),
        help_text=_("Send email signature request link to the holder on checkout.")
    )
    category = models.ForeignKey(
        to='assets.Category',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='custody_templates',
        verbose_name=_("Category"),
        help_text=_("Target Category this template overrides for the selected Tenant scope.")
    )
    tags = models.ManyToManyField(
        to='extras.Tag',
        related_name='custody_templates',
        blank=True,
        verbose_name=_("Tags")
    )

    class Meta:
        ordering = ('name',)
        verbose_name = _("Custody Template")
        verbose_name_plural = _("Custody Templates")

    def __str__(self):
        if self.tenant:
            return f"{self.tenant} - {self.name}"
        if self.tenant_group:
            return f"Group: {self.tenant_group} - {self.name}"
        return f"Global - {self.name}"


class CustodyReceipt(ChangeLoggingMixin, BaseModel):
    # Tenant scoping is enforced at the API layer (CustodyReceiptViewSet uses
    # _scope_by_asset_tenant). The default manager is intentionally unscoped so
    # the token-based public sign view (custody_eula_sign) can resolve a receipt
    # by its secret token regardless of the requester's tenant context.
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    ACCEPTANCE_STATUS_CHOICES = [
        (STATUS_PENDING, _('Pending')),
        (STATUS_ACCEPTED, _('Accepted')),
        (STATUS_DECLINED, _('Declined')),
    ]

    asset = models.ForeignKey('assets.Asset', on_delete=models.PROTECT, related_name='custody_receipts', db_index=True, verbose_name=_("Asset"))
    holder = models.ForeignKey('organization.AssetHolder', on_delete=models.PROTECT, related_name='custody_receipts', verbose_name=_("Holder"))
    token = models.CharField(max_length=64, unique=True, default=generate_token)
    custody_template = models.ForeignKey(
        to='compliance.CustodyTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='receipts',
        verbose_name=_("Custody Template")
    )
    signature_provider = models.CharField(max_length=50, default='local', verbose_name=_("Signature Provider"))
    eula_text = models.TextField(blank=True, verbose_name=_("EULA Text"))
    disclaimer = models.TextField(blank=True, verbose_name=_("Disclaimer"))
    qms_reference = models.CharField(max_length=100, blank=True, verbose_name=_("QMS Reference"))
    accepted = models.BooleanField(default=False, verbose_name=_("Accepted"))
    accepted_date = models.DateTimeField(null=True, blank=True, verbose_name=_("Accepted Date"))
    acceptance_method = models.CharField(max_length=50, default='link', verbose_name=_("Acceptance Method"))
    acceptance_status = models.CharField(max_length=20, choices=ACCEPTANCE_STATUS_CHOICES, default=STATUS_PENDING, db_index=True, verbose_name=_("Acceptance Status"))
    signature_data = models.TextField(blank=True, verbose_name=_("Signature Data"))
    signature_hash = models.CharField(max_length=64, blank=True)
    verification_hash = models.CharField(max_length=64, unique=True, blank=True, null=True)  # null=True intentional: unique constraint allows multiple unsigned (NULL) receipts
    signature_canvas = models.TextField(blank=True, verbose_name=_("Signature Canvas"), help_text=_("Base64 canvas stroke vector string representation"))
    signed_at = models.DateTimeField(default=timezone.now, verbose_name=_("Signed At"))
    eula_version = models.CharField(max_length=10, default='1.0', verbose_name=_("EULA Version"))
    created_date = models.DateTimeField(auto_now_add=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("IP Address"))
    user_agent = models.TextField(blank=True, verbose_name=_("User Agent"))

    class Meta:
        ordering = ('-signed_at',)
        verbose_name = _("Custody Receipt")
        verbose_name_plural = _("Custody Receipts")

    def __str__(self):
        return f"Custody Receipt for {self.asset} signed by {self.holder} (EULA v{self.eula_version})"


User = get_user_model()


class AuditSession(StandardModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    name = models.CharField(max_length=200, verbose_name=_("Name"))
    tenant = models.ForeignKey(
        to='organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='audit_sessions',
        verbose_name=_("Tenant"),
        help_text=_('Tenant this campaign belongs to. Leave blank for MSP-wide / global sessions.'),
    )
    location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Location"),
        help_text=_("Target location expected to be audited. If omitted, applies globally.")
    )
    status = models.CharField(
        max_length=20,
        choices=AuditSessionStatusChoices.choices,
        default=AuditSessionStatusChoices.PLANNED,
        verbose_name=_("Status"),
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Completed At"))
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='audit_sessions', verbose_name=_("Created By"))
    reconciliation_report = models.JSONField(
        null=True, blank=True, editable=False,
        help_text=_("Frozen reconciliation report written at close time. Denormalized for long-term readability.")
    )

    class Meta:
        ordering = ['-started_at']
        verbose_name = _("Audit Session")
        verbose_name_plural = _("Audit Sessions")

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse('compliance:auditsession_detail', kwargs={'pk': self.pk})

    @property
    def expected_assets_queryset(self):
        """Return the set of assets this session expects to audit.

        Bypasses ambient tenant scoping: when the session is tenant-scoped it filters
        explicitly by session.tenant; when global (tenant=None) no tenant filter is
        applied, so the queryset is stable regardless of which viewer calls it.
        """
        from assets.models import Asset, StatusLabel
        from django.db.models import QuerySet
        # Raw queryset — bypasses TenantScopingSoftDeleteManager ambient filter.
        qs = QuerySet(model=Asset).filter(deleted_at__isnull=True)
        if self.tenant_id is not None:
            qs = qs.filter(tenant_id=self.tenant_id)
        qs = qs.exclude(status__type=StatusLabel.TYPE_ARCHIVED)
        if not self.location:
            return qs.filter(status__type__in=[
                StatusLabel.TYPE_DEPLOYABLE,
                StatusLabel.TYPE_PENDING,
                StatusLabel.TYPE_DEPLOYED
            ])
        return qs.filter(location=self.location)


class AssetAudit(ChangeLoggingMixin, models.Model):
    # Tenant scoping is enforced at the API layer (AssetAuditViewSet uses
    # _scope_by_asset_tenant). The default manager is intentionally unscoped:
    # audit classification/reconciliation reads `session.audits` and must see
    # every audit in a session regardless of the viewer's tenant context.

    # AssetAudit carries no tenant of its own. Its owning tenant is the audited
    # asset's tenant (`asset.tenant`), NOT the session's: a session may be a
    # global / MSP-wide record (AuditSession.tenant is null for those), whereas
    # the asset always belongs to exactly one tenant. Attributing the changelog
    # to asset.tenant keeps each audit change visible to the asset's owner even
    # in no-ambient-tenant flows (superuser global session, service audit_asset).
    changelog_tenant_lookup = 'asset__tenant'

    session = models.ForeignKey(
        AuditSession,
        on_delete=models.CASCADE,
        related_name='audits',
        null=True,
        blank=True,
        verbose_name=_("Session")
    )
    # SET_NULL preserves audit history as orphaned records when an asset is
    # hard-purged via purge_deleted, rather than destroying compliance evidence.
    asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audits',
        verbose_name=_("Asset"),
    )
    auditor = models.ForeignKey(User, on_delete=models.PROTECT, related_name='audits_performed', verbose_name=_("Auditor"))
    timestamp = models.DateTimeField(auto_now_add=True)
    location = models.ForeignKey(
        'organization.Location',
        on_delete=models.PROTECT,
        verbose_name=_("Location"),
        help_text=_("The observed physical location of the asset during audit.")
    )
    status = models.ForeignKey(
        'assets.StatusLabel',
        on_delete=models.PROTECT,
        verbose_name=_("Status"),
        help_text=_("The observed physical status of the asset during audit.")
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    verification_method = models.CharField(
        max_length=30,
        choices=AuditVerificationMethodChoices.choices,
        default=AuditVerificationMethodChoices.MANUAL,
        verbose_name=_("Verification Method"),
    )

    class Meta:
        ordering = ['-timestamp']
        constraints = [
            models.UniqueConstraint(fields=['session', 'asset'], name='unique_session_asset')
        ]
        verbose_name = _("Asset Audit")
        verbose_name_plural = _("Asset Audits")
