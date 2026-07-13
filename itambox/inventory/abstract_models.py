from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from core.models import BaseModel, ChangeLoggingMixin, DeletableVaultModel
from core.mixins import TaggableMixin, AutoSlugMixin, SoftDeleteMixin, JournalingMixin, SubscribableMixin, CustomFieldDataMixin


from .mixins import CheckableInventoryModelMixin


class AbstractInventoryItem(CustomFieldDataMixin, CheckableInventoryModelMixin, AutoSlugMixin, SubscribableMixin, DeletableVaultModel):
    allow_global_tenant = True
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    slug = models.SlugField(max_length=255, verbose_name=_("Slug"))
    manufacturer = models.ForeignKey(
        'assets.Manufacturer',
        on_delete=models.PROTECT,
        related_name='%(class)ss',
        verbose_name=_("Manufacturer")
    )
    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='%(class)ss',
        verbose_name=_("Category"),
        db_index=True
    )
    supplier = models.ForeignKey(
        'assets.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supplier_%(class)ss',
        verbose_name=_("Supplier"),
        db_index=True
    )
    part_number = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name=_("Part Number"),
        help_text=_("SKU or manufacturer part number")
    )
    ean = models.CharField(
        max_length=14,
        blank=True,
        db_index=True,
        verbose_name=_("EAN"),
        help_text=_("Barcode (EAN / UPC / GTIN) — scannable to open this item.")
    )
    min_qty = models.PositiveIntegerField(
        default=0,
        blank=True,
        verbose_name=_("Safety Threshold"),
        help_text=_("Alert threshold quantity")
    )
    allow_overallocate = models.BooleanField(
        default=False,
        verbose_name=_("Allow Over-allocation"),
        help_text=_("Allow checkout count to exceed stock capacity")
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='%(class)ss',
        verbose_name=_("Tenant"),
        db_index=True
    )
    tags = models.ManyToManyField(
        'extras.Tag',
        related_name='%(app_label)s_%(class)s',
        verbose_name=_("Tags"),
        blank=True
    )

    slug_source = ('manufacturer__name', 'name')

    class Meta:
        abstract = True
        ordering = ('manufacturer', 'name')
        constraints = [
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_%(class)s_slug_active'),
        ]

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"


class AbstractStock(ChangeLoggingMixin, BaseModel):
    location = models.ForeignKey(
        'organization.Location',
        on_delete=models.PROTECT,
        related_name='%(class)s_stocks',
        verbose_name=_("Location"),
        db_index=True
    )
    # ADR-0001 phase 4: a pool is owned by its location's tenant — always
    # derived, never client-supplied (save()/clean() below). Tenant scoping
    # uses THIS field, not the catalogue item's tenant: a global item stays
    # visible everywhere, its stock does not become global.
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        related_name='%(class)ss',
        db_index=True,
        editable=False,
        verbose_name=_("Tenant"),
        help_text=_("Owning tenant — always the stock location's tenant."),
    )
    # Signed: when an item allows over-allocation, a checkout can drive on-hand
    # below zero. The balance must be able to represent that deficit so check-in
    # restores symmetrically instead of materialising phantom stock. Non-over-
    # allocatable items are guarded against going negative in adjust_inventory_stock.
    qty = models.IntegerField(default=0, verbose_name=_("Quantity"))

    class Meta:
        abstract = True
        ordering = ('location',)

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.location_id:
            location_tenant_id = self.location.tenant_id
            if location_tenant_id is None:
                raise ValidationError({'location': _(
                    "Stock requires a location owned by a tenant — assign the "
                    "location to a tenant first."
                )})
            # Derive rather than reject: the tenant is not user-editable.
            self.tenant_id = location_tenant_id

    def save(self, *args, **kwargs):
        # Derivation also happens here for flows that bypass full_clean();
        # the pre_save validator then re-runs clean() and re-confirms.
        if self.location_id and self.tenant_id != self.location.tenant_id:
            self.tenant_id = self.location.tenant_id
        super().save(*args, **kwargs)


class AbstractAssignment(JournalingMixin, TaggableMixin, SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    #: Name of the concrete catalogue-item FK ('accessory' / 'consumable' /
    #: 'component') and the matching stock model label — set by each concrete
    #: assignment model.
    _item_attr = None
    _stock_model_label = None

    assigned_holder = models.ForeignKey(
        'organization.AssetHolder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_assignments',
        verbose_name=_("Assigned Holder"),
        db_index=True
    )
    assigned_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_assignments',
        verbose_name=_("Assigned Location"),
        db_index=True
    )
    assigned_asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_assignments',
        verbose_name=_("Assigned Asset"),
        db_index=True
    )
    from_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_checkouts',
        verbose_name=_("From Location"),
        db_index=True
    )
    qty = models.PositiveIntegerField(default=1, verbose_name=_("Checkout Quantity"))
    assigned_date = models.DateTimeField(default=timezone.now, verbose_name=_("Assigned Date"))
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    tags = models.ManyToManyField('extras.Tag', related_name='%(class)s_assignments', verbose_name=_("Tags"), blank=True)

    # --- ADR-0001 phase 4: historical ownership + grant provenance ---------
    # Derived on save, nullable because pre-remediation history may not be
    # reconstructable (see the phase-1 integrity report's ambiguous class).
    source_tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        related_name='+',
        null=True, blank=True, editable=False,
        verbose_name=_("Source tenant"),
        help_text=_("Tenant owning the source stock/location at assignment time."),
    )
    target_tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        related_name='+',
        null=True, blank=True, editable=False,
        verbose_name=_("Target tenant"),
        help_text=_("Tenant of the destination holder/location/asset at assignment time."),
    )
    resource_grant = models.ForeignKey(
        'organization.TenantResourceGrant',
        on_delete=models.PROTECT,
        related_name='%(class)ss',
        null=True, blank=True, editable=False,
        verbose_name=_("Resource grant"),
        help_text=_("The grant that authorized a cross-tenant assignment. "
                    "Stays as history after the grant is revoked."),
    )

    class Meta:
        abstract = True
        ordering = ('-assigned_date',)

    def __str__(self):
        recipient = self.assigned_holder or self.assigned_location or self.assigned_asset or "Unknown"
        return f"{self.qty}x assigned to {recipient}"

    # ------------------------------------------------------------ provenance
    def _derive_source_tenant_id(self):
        """Owner of the source pool: from-location's tenant when a concrete
        pool is referenced, else the catalogue item's tenant (None for a
        global item without a pool — no owner is derivable)."""
        if self.from_location_id:
            return self.from_location.tenant_id
        item = getattr(self, self._item_attr, None)
        return item.tenant_id if item is not None else None

    def _derive_target_tenant_id(self):
        for target in (self.assigned_holder, self.assigned_location, self.assigned_asset):
            if target is not None:
                return target.tenant_id
        return None

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        self.source_tenant_id = self._derive_source_tenant_id()
        self.target_tenant_id = self._derive_target_tenant_id()

        cross_tenant = (
            self.source_tenant_id is not None
            and self.target_tenant_id is not None
            and self.source_tenant_id != self.target_tenant_id
        )
        if not cross_tenant:
            # Same-tenant assignments carry no grant (ADR-0001 phase 4).
            self.resource_grant = None
            return
        # A cross-tenant assignment must reference a grant that covers the
        # source pool and the target tenant AT CREATION TIME. Once created,
        # the row is history: never re-validated against a later revocation.
        if self.pk is not None:
            return
        grant = self.resource_grant
        if grant is None:
            raise ValidationError(_(
                "Cross-tenant assignment requires an explicit resource grant "
                "from the owning tenant (ADR-0001). No grant was resolved."
            ))
        problems = self._grant_coverage_problems(grant)
        if problems:
            raise ValidationError(_(
                "The referenced resource grant does not authorize this "
                "assignment: %(problems)s"
            ) % {'problems': '; '.join(problems)})

    def _grant_coverage_problems(self, grant):
        """Structural coverage check — RBAC is the resolver's/service's job."""
        # inline import: breaks an inventory <-> organization import cycle at load
        from organization.access import get_ancestor_tenant_group_ids
        from organization.models import TenantResourceGrant

        problems = []
        if grant.deleted_at is not None:
            problems.append('the grant is revoked')
        if grant.tenant_id != self.source_tenant_id:
            problems.append('the grant is not from the source tenant')
        if grant.access_level != TenantResourceGrant.ACCESS_USE:
            problems.append("the grant does not allow 'use'")
        stock = self._source_stock()
        if stock is None:
            problems.append('no concrete source pool (item + from-location) exists')
        elif (grant.resource_type.model_class() is not type(stock)
                or grant.resource_id != stock.pk):
            problems.append('the grant covers a different stock pool')
        if grant.grantee_tenant_id is not None:
            if grant.grantee_tenant_id != self.target_tenant_id:
                problems.append('the grant is for a different tenant')
        else:
            target_group_id = (
                grant.grantee_tenant_group_id is not None
                and self.target_tenant is not None
                and self.target_tenant.group_id
            )
            if not target_group_id or grant.grantee_tenant_group_id not in \
                    get_ancestor_tenant_group_ids(target_group_id, live_only=True):
                problems.append("the grant's tenant group does not cover the target tenant")
        return problems

    def _source_stock(self):
        """The concrete stock pool this assignment draws from, or None.

        ``_base_manager``: the pool may belong to another tenant (that is the
        whole point of a cross-tenant assignment), so the tenant-scoped
        default manager must not hide it.
        """
        if not self.from_location_id:
            return None
        item_id = getattr(self, f'{self._item_attr}_id', None)
        if not item_id:
            return None
        from django.apps import apps
        stock_model = apps.get_model(self._stock_model_label)
        return stock_model._base_manager.filter(**{
            f'{self._item_attr}_id': item_id,
            'location_id': self.from_location_id,
        }).first()
