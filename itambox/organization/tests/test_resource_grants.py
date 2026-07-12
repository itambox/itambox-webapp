"""TenantResourceGrant (ADR-0001, remediation plan phase 2).

Model-layer semantics only — the resolver service (phase 3) and the
inventory wiring (phase 4) have their own suites. Grants are validated on
save by the global pre_save validator (which runs ``clean()``), so invalid
states are exercised both at the validation layer (friendly errors) and at
the DB layer (constraints, via ``bulk_create`` which skips signals).
"""
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from assets.models import Manufacturer
from inventory.models import Accessory, AccessoryStock
from organization.models import (
    Location, Site, Tenant, TenantGroup, TenantResourceGrant,
)


def _build_world():
    """Provider + managed tenant + a stock pool at a provider location."""
    provider = Tenant.objects.create(name='TRG Provider', slug='trg-provider', is_provider=True)
    managed = Tenant.objects.create(name='TRG Managed', slug='trg-managed', managed_by=provider)
    other = Tenant.objects.create(name='TRG Other', slug='trg-other')
    site = Site.objects.create(name='TRG Site', slug='trg-site', tenant=provider)
    location = Location.objects.create(
        name='TRG Depot', slug='trg-depot', site=site, tenant=provider,
    )
    manufacturer = Manufacturer.objects.create(name='TRG Mfg', slug='trg-mfg')
    accessory = Accessory.objects.create(
        name='TRG Dock', slug='trg-dock', manufacturer=manufacturer, tenant=provider,
    )
    stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=10)
    return provider, managed, other, location, accessory, stock


def _stock_ct():
    return ContentType.objects.get_for_model(AccessoryStock)


class TenantResourceGrantValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        (cls.provider, cls.managed, cls.other,
         cls.location, cls.accessory, cls.stock) = _build_world()

    def _grant_kwargs(self, **overrides):
        kwargs = dict(
            tenant=self.provider,
            grantee_tenant=self.managed,
            resource_type=_stock_ct(),
            resource_id=self.stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )
        kwargs.update(overrides)
        return kwargs

    def test_valid_direct_grant_saves(self):
        grant = TenantResourceGrant.objects.create(**self._grant_kwargs())
        assert grant.is_active
        assert grant.resource == self.stock

    def test_valid_group_grant_saves(self):
        group = TenantGroup.objects.create(name='TRG Group', slug='trg-group')
        grant = TenantResourceGrant.objects.create(**self._grant_kwargs(
            grantee_tenant=None, grantee_tenant_group=group,
        ))
        assert grant.grantee_tenant_group == group

    def test_grant_to_unrelated_tenant_is_allowed(self):
        # ADR-0001: the grant itself is the authorization — no relationship
        # prerequisite is enforced at the model layer.
        TenantResourceGrant.objects.create(**self._grant_kwargs(grantee_tenant=self.other))

    def test_both_grantees_rejected(self):
        group = TenantGroup.objects.create(name='TRG G2', slug='trg-g2')
        with self.assertRaises(ValidationError):
            TenantResourceGrant.objects.create(**self._grant_kwargs(
                grantee_tenant_group=group,
            ))

    def test_no_grantee_rejected(self):
        with self.assertRaises(ValidationError):
            TenantResourceGrant.objects.create(**self._grant_kwargs(grantee_tenant=None))

    def test_owner_as_grantee_rejected(self):
        with self.assertRaises(ValidationError):
            TenantResourceGrant.objects.create(**self._grant_kwargs(
                grantee_tenant=self.provider,
            ))

    def test_unapproved_resource_model_rejected(self):
        tenant_ct = ContentType.objects.get_for_model(Tenant)
        with self.assertRaises(ValidationError):
            TenantResourceGrant.objects.create(**self._grant_kwargs(
                resource_type=tenant_ct, resource_id=self.managed.pk,
            ))

    def test_missing_resource_rejected(self):
        with self.assertRaises(ValidationError):
            TenantResourceGrant.objects.create(**self._grant_kwargs(
                resource_id=self.stock.pk + 999_999,
            ))

    def test_resource_owned_by_other_tenant_rejected(self):
        # The pool sits at a provider-owned location; the managed tenant
        # cannot claim to be its owner.
        with self.assertRaises(ValidationError):
            TenantResourceGrant.objects.create(**self._grant_kwargs(
                tenant=self.managed, grantee_tenant=self.other,
            ))

    def test_resource_at_tenantless_location_rejected(self):
        site = Site.objects.create(name='TRG NoT Site', slug='trg-not-site')
        loc = Location.objects.create(name='TRG NoT', slug='trg-not', site=site)
        orphan_stock = AccessoryStock.objects.create(
            accessory=self.accessory, location=loc, qty=1,
        )
        with self.assertRaises(ValidationError):
            TenantResourceGrant.objects.create(**self._grant_kwargs(
                resource_id=orphan_stock.pk,
            ))


class TenantResourceGrantConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        (cls.provider, cls.managed, cls.other,
         cls.location, cls.accessory, cls.stock) = _build_world()

    def _grant_kwargs(self, **overrides):
        kwargs = dict(
            tenant=self.provider,
            grantee_tenant=self.managed,
            resource_type=_stock_ct(),
            resource_id=self.stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )
        kwargs.update(overrides)
        return kwargs

    def test_db_rejects_double_grantee(self):
        # bulk_create skips the pre_save validator — the CHECK constraint is
        # the last line of defense.
        group = TenantGroup.objects.create(name='TRG CG', slug='trg-cg')
        with self.assertRaises(IntegrityError), transaction.atomic():
            TenantResourceGrant.objects.bulk_create([
                TenantResourceGrant(**self._grant_kwargs(grantee_tenant_group=group)),
            ])

    def test_db_rejects_owner_grantee_identity(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TenantResourceGrant.objects.bulk_create([
                TenantResourceGrant(**self._grant_kwargs(grantee_tenant=self.provider)),
            ])

    def test_one_active_grant_per_resource_and_grantee(self):
        TenantResourceGrant.objects.create(**self._grant_kwargs())
        with self.assertRaises(IntegrityError), transaction.atomic():
            TenantResourceGrant.objects.create(**self._grant_kwargs(
                access_level=TenantResourceGrant.ACCESS_VIEW,
            ))

    def test_revoke_then_regrant_is_allowed(self):
        grant = TenantResourceGrant.objects.create(**self._grant_kwargs())
        grant.delete()  # soft revoke
        grant.refresh_from_db()
        assert grant.deleted_at is not None
        assert not TenantResourceGrant.objects.filter(pk=grant.pk).exists()
        assert TenantResourceGrant._base_manager.filter(pk=grant.pk).exists()
        # Same (resource, grantee) may be granted again after revocation.
        TenantResourceGrant.objects.create(**self._grant_kwargs())

    def test_second_grantee_tenant_gets_own_active_grant(self):
        TenantResourceGrant.objects.create(**self._grant_kwargs())
        TenantResourceGrant.objects.create(**self._grant_kwargs(grantee_tenant=self.other))


class TenantResourceGrantOrphanCleanupTests(TestCase):
    def test_deleting_stock_revokes_active_grants(self):
        provider, managed, other, location, accessory, stock = _build_world()
        grant = TenantResourceGrant.objects.create(
            tenant=provider,
            grantee_tenant=managed,
            resource_type=_stock_ct(),
            resource_id=stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )
        stock.delete()  # stock is hard-delete (no soft-delete mixin)
        grant.refresh_from_db()
        assert grant.deleted_at is not None, (
            'hard-deleting the pool must revoke (soft-delete) its grants'
        )

    def test_deleting_stock_leaves_revoked_grants_untouched(self):
        provider, managed, other, location, accessory, stock = _build_world()
        grant = TenantResourceGrant.objects.create(
            tenant=provider,
            grantee_tenant=managed,
            resource_type=_stock_ct(),
            resource_id=stock.pk,
            access_level=TenantResourceGrant.ACCESS_VIEW,
        )
        grant.delete()
        grant.refresh_from_db()
        first_revocation = grant.deleted_at
        stock.delete()
        grant.refresh_from_db()
        assert grant.deleted_at == first_revocation
