"""AssetTagSequence — tenant-scoped auto-numbering sequences for asset tags."""
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse

from core.models import BaseModel
from core.mixins import SoftDeleteMixin
from core.models import ChangeLoggingMixin
from core.managers import TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager


class AssetTagSequence(ChangeLoggingMixin, BaseModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='tag_sequences',
        db_index=True,
        help_text=_("The tenant owning this sequence. Null represents system-wide/global sequences.")
    )
    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='tag_sequences',
        db_index=True,
        help_text=_("The asset category this sequence applies to. Null represents default sequences.")
    )
    prefix = models.CharField(max_length=20, default='ASSET-', help_text=_("Prefix for generated asset tags (e.g. ASSET-)"))
    next_value = models.PositiveIntegerField(default=1)
    zero_padding = models.PositiveSmallIntegerField(default=6)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Asset Tag Sequence")
        verbose_name_plural = _("Asset Tag Sequences")
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'prefix'],
                condition=models.Q(tenant__isnull=False) & models.Q(deleted_at__isnull=True),
                name='unique_tenant_prefix'
            ),
            models.UniqueConstraint(
                fields=['prefix'],
                condition=models.Q(tenant__isnull=True) & models.Q(deleted_at__isnull=True),
                name='unique_global_prefix'
            )
        ]

    def __str__(self):
        return f'{self.prefix} (next: {self.next_value:0{self.zero_padding}d})'

    def get_absolute_url(self):
        return reverse('assets:assettagsequence_detail', kwargs={'pk': self.pk})

    def next_tag(self):
        from django.db import transaction
        from django.db.models import F
        # Lock the sequence row before reading: formatting the tag from an unlocked
        # read lets two concurrent saves claim the same value and collide on the
        # asset_tag unique constraint.
        with transaction.atomic():
            locked = type(self)._base_manager.select_for_update().get(pk=self.pk)
            tag = f'{locked.prefix}{locked.next_value:0{locked.zero_padding}d}'
            type(self)._base_manager.filter(pk=self.pk).update(next_value=F('next_value') + 1)
        self.refresh_from_db(fields=['next_value'])
        return tag

    @property
    def next_tag_preview(self):
        return f'{self.prefix}{self.next_value:0{self.zero_padding}d}'

    @classmethod
    def get_next_tag_for_asset(cls, asset):
        """
        Resolves the next asset tag for the given asset based on a hierarchical fallback chain:
        1. Tenant-specific + Category-specific sequence
        2. Tenant-specific default sequence (no category)
        3. Global + Category-specific sequence
        4. Global default sequence (prefix='ASSET-', created if missing)
        """
        # 1. Tenant + Category specific
        if asset.tenant and asset.category:
            seq = cls.all_objects.filter(tenant=asset.tenant, category=asset.category, is_active=True).first()
            if seq:
                return seq.next_tag()

        # 2. Tenant default (no category)
        if asset.tenant:
            seq = cls.all_objects.filter(tenant=asset.tenant, category__isnull=True, is_active=True).first()
            if seq:
                return seq.next_tag()

        # 3. Global + Category specific
        if asset.category:
            seq = cls.all_objects.filter(tenant__isnull=True, category=asset.category, is_active=True).first()
            if seq:
                return seq.next_tag()

        # 4. Global default (no tenant, no category, prefix='ASSET-')
        seq, _ = cls.all_objects.get_or_create(
            tenant__isnull=True,
            category__isnull=True,
            prefix='ASSET-',
            defaults={'next_value': 1, 'zero_padding': 6, 'is_active': True}
        )
        return seq.next_tag()

    @classmethod
    def resolve_sequence_for_asset(cls, asset):
        """
        Resolves the matching sequence object for the asset based on the fallback chain.
        Does not increment or modify the sequence.
        """
        # 1. Tenant + Category specific
        if asset.tenant and asset.category:
            seq = cls.all_objects.filter(tenant=asset.tenant, category=asset.category, is_active=True).first()
            if seq:
                return seq

        # 2. Tenant default (no category)
        if asset.tenant:
            seq = cls.all_objects.filter(tenant=asset.tenant, category__isnull=True, is_active=True).first()
            if seq:
                return seq

        # 3. Global + Category specific
        if asset.category:
            seq = cls.all_objects.filter(tenant__isnull=True, category=asset.category, is_active=True).first()
            if seq:
                return seq

        # 4. Global default (no tenant, no category, prefix='ASSET-')
        seq, _ = cls.all_objects.get_or_create(
            tenant__isnull=True,
            category__isnull=True,
            prefix='ASSET-',
            defaults={'next_value': 1, 'zero_padding': 6, 'is_active': True}
        )
        return seq
