"""resolve_stock_access — the centralized resource-access resolver (phase 3).

Verifies the six-step ADR-0001 flow: owner resolution from the pool's
location, same-tenant short-circuit, direct/ancestor-group grant lookup,
access-level comparison, the independent RBAC check in the ACTIVE tenant,
and provenance (the exact grant row is returned). Plus the two hard
invariants: non-transitivity and no-grant-no-access (superusers included).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from assets.models import Manufacturer
from core.tests.mixins import TenantTestMixin, grant
from inventory.models import Accessory, AccessoryStock
from organization.models import (
    Location, Role, Site, Tenant, TenantGroup, TenantResourceGrant,
)
from organization.services import (
    DENIED_INSUFFICIENT_LEVEL, DENIED_NO_ACTIVE_TENANT, DENIED_NO_GRANT,
    DENIED_OWNER_UNRESOLVABLE, DENIED_RBAC, REASON_DIRECT_GRANT,
    REASON_GROUP_GRANT, REASON_SAME_TENANT, resolve_stock_access,
)

User = get_user_model()

PERM = 'inventory.add_accessoryassignment'


class ResolveStockAccessTests(TenantTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Tenant.objects.create(name='RA Owner', slug='ra-owner')
        cls.root_group = TenantGroup.objects.create(name='RA Root', slug='ra-root')
        cls.child_group = TenantGroup.objects.create(
            name='RA Child', slug='ra-child', parent=cls.root_group,
        )
        cls.sibling_group = TenantGroup.objects.create(
            name='RA Sibling', slug='ra-sibling', parent=cls.root_group,
        )
        cls.grantee = Tenant.objects.create(
            name='RA Grantee', slug='ra-grantee', group=cls.child_group,
        )
        cls.third = Tenant.objects.create(name='RA Third', slug='ra-third')

        site = Site.objects.create(name='RA Site', slug='ra-site', tenant=cls.owner)
        cls.location = Location.objects.create(
            name='RA Depot', slug='ra-depot', site=site, tenant=cls.owner,
        )
        manufacturer = Manufacturer.objects.create(name='RA Mfg', slug='ra-mfg')
        cls.accessory = Accessory.objects.create(
            name='RA Dock', slug='ra-dock', manufacturer=manufacturer, tenant=cls.owner,
        )
        cls.stock = AccessoryStock.objects.create(
            accessory=cls.accessory, location=cls.location, qty=10,
        )

        # Grantee-side technician holding PERM in the grantee tenant.
        cls.tech = User.objects.create_user(username='ra-tech', password='x')
        role = Role.objects.create(
            tenant=cls.grantee, name='RA Tech', permissions=[PERM],
        )
        grant(cls.tech, cls.grantee, role)

    def _use_grant(self, **overrides):
        kwargs = dict(
            tenant=self.owner,
            grantee_tenant=self.grantee,
            resource_type=None,
            resource_id=self.stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )
        kwargs.update(overrides)
        if kwargs['resource_type'] is None:
            from django.contrib.contenttypes.models import ContentType
            kwargs['resource_type'] = ContentType.objects.get_for_model(AccessoryStock)
        return TenantResourceGrant.objects.create(**kwargs)

    # ------------------------------------------------------------- same tenant
    def test_same_tenant_rbac_only(self):
        owner_role = Role.objects.create(
            tenant=self.owner, name='RA Owner Role', permissions=[PERM],
        )
        owner_user = User.objects.create_user(username='ra-owner-user', password='x')
        self.grant(owner_user, self.owner, owner_role)
        decision = resolve_stock_access(
            owner_user, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.owner,
        )
        assert decision.allowed
        assert decision.reason == REASON_SAME_TENANT
        assert decision.grant is None

    def test_same_tenant_without_perm_denied(self):
        nobody = User.objects.create_user(username='ra-nobody', password='x')
        decision = resolve_stock_access(
            nobody, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.owner,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_RBAC

    # ------------------------------------------------------------ cross tenant
    def test_no_grant_denied(self):
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_direct_grant_allows_and_returns_grant(self):
        grant_row = self._use_grant()
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed
        assert decision.reason == REASON_DIRECT_GRANT
        assert decision.grant == grant_row
        assert decision.owner_tenant_id == self.owner.pk

    def test_view_grant_does_not_cover_use(self):
        self._use_grant(access_level=TenantResourceGrant.ACCESS_VIEW)
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_INSUFFICIENT_LEVEL

    def test_use_grant_covers_view(self):
        self._use_grant()
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_VIEW, PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed

    def test_revoked_grant_denied(self):
        grant_row = self._use_grant()
        grant_row.delete()
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_grant_without_rbac_denied(self):
        self._use_grant()
        nobody = User.objects.create_user(username='ra-nobody2', password='x')
        decision = resolve_stock_access(
            nobody, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_RBAC
        assert decision.grant is not None  # the grant existed; the USER failed

    # ------------------------------------------------------------ group grants
    def test_group_grant_covers_descendant_group_tenant(self):
        grant_row = self._use_grant(
            grantee_tenant=None, grantee_tenant_group=self.root_group,
        )
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,  # group chain: child -> root
        )
        assert decision.allowed
        assert decision.reason == REASON_GROUP_GRANT
        assert decision.grant == grant_row

    def test_group_grant_on_own_group(self):
        self._use_grant(grantee_tenant=None, grantee_tenant_group=self.child_group)
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed

    def test_group_grant_on_sibling_group_denied(self):
        self._use_grant(grantee_tenant=None, grantee_tenant_group=self.sibling_group)
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_tenant_without_group_ignores_group_grants(self):
        self._use_grant(grantee_tenant=None, grantee_tenant_group=self.root_group)
        role = Role.objects.create(tenant=self.third, name='RA T3', permissions=[PERM])
        user3 = User.objects.create_user(username='ra-user3', password='x')
        self.grant(user3, self.third, role)
        decision = resolve_stock_access(
            user3, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.third,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    # -------------------------------------------------------------- invariants
    def test_non_transitive(self):
        # owner -> grantee is granted; grantee -> third is granted on some
        # OTHER pool. third must still have no access to owner's pool.
        self._use_grant()
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.third,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_superuser_needs_grant_for_cross_tenant(self):
        boss = User.objects.create_superuser(username='ra-boss', password='x')
        decision = resolve_stock_access(
            boss, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT
        self._use_grant()
        decision = resolve_stock_access(
            boss, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed

    def test_no_active_tenant_denied(self):
        self.clear_tenant_context()
        decision = resolve_stock_access(
            self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_ACTIVE_TENANT

    def test_owner_unresolvable_denied(self):
        # ADR-0001 phase 4: AbstractStock now derives+requires a tenant from
        # its location at creation time, so a pool can no longer be created
        # directly at a tenant-less location. Reproduce an unresolvable owner
        # by clearing the location's tenant AFTER the stock already exists
        # (e.g. tenant offboarding leaving a stray pool behind).
        site = Site.objects.create(name='RA NoT Site', slug='ra-not-site')
        loc = Location.objects.create(
            name='RA NoT', slug='ra-not', site=site, tenant=self.owner,
        )
        stray = AccessoryStock.objects.create(
            accessory=self.accessory, location=loc, qty=1,
        )
        loc.tenant = None
        loc.save()
        decision = resolve_stock_access(
            self.tech, stray, TenantResourceGrant.ACCESS_USE, PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_OWNER_UNRESOLVABLE

    def test_active_tenant_defaults_to_context(self):
        self._use_grant()
        with self.tenant_context(self.grantee):
            decision = resolve_stock_access(
                self.tech, self.stock, TenantResourceGrant.ACCESS_USE, PERM,
            )
        assert decision.allowed
