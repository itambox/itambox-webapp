"""resolve_stock_access — the centralized resource-access resolver (phase 3).

Verifies the six-step ADR-0001 flow: owner resolution from the pool's
location, same-tenant short-circuit, direct/ancestor-group grant lookup,
access-level comparison, the independent RBAC check in the ACTIVE tenant,
and provenance (the exact grant row is returned). Plus the two hard
invariants: non-transitivity and no-grant-no-access (superusers included).
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from assets.models import Manufacturer
from core.context import (
    SystemAuthorizationContext,
    _issue_system_authorization,
    set_current_tenant,
)
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin, grant
from inventory.models import Accessory, AccessoryStock
from organization.models import (
    Location,
    Role,
    Site,
    Tenant,
    TenantGroup,
    TenantResourceGrant,
)
from organization.services import (
    DENIED_INSUFFICIENT_LEVEL,
    DENIED_NO_ACTIVE_TENANT,
    DENIED_NO_GRANT,
    DENIED_OWNER_UNRESOLVABLE,
    DENIED_RBAC,
    REASON_DIRECT_GRANT,
    REASON_GROUP_GRANT,
    REASON_SAME_TENANT,
    resolve_stock_access,
    resolved_shared_stock_ids,
)

User = get_user_model()

PERM = "inventory.add_accessoryassignment"


class ResolveStockAccessTests(TenantTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Tenant.objects.create(name="RA Owner", slug="ra-owner")
        cls.root_group = TenantGroup.objects.create(name="RA Root", slug="ra-root")
        cls.child_group = TenantGroup.objects.create(
            name="RA Child",
            slug="ra-child",
            parent=cls.root_group,
        )
        cls.sibling_group = TenantGroup.objects.create(
            name="RA Sibling",
            slug="ra-sibling",
            parent=cls.root_group,
        )
        cls.grantee = Tenant.objects.create(
            name="RA Grantee",
            slug="ra-grantee",
            group=cls.child_group,
        )
        cls.third = Tenant.objects.create(name="RA Third", slug="ra-third")

        site = Site.objects.create(name="RA Site", slug="ra-site", tenant=cls.owner)
        cls.location = Location.objects.create(
            name="RA Depot",
            slug="ra-depot",
            site=site,
            tenant=cls.owner,
        )
        manufacturer = Manufacturer.objects.create(name="RA Mfg", slug="ra-mfg")
        cls.accessory = Accessory.objects.create(
            name="RA Dock",
            slug="ra-dock",
            manufacturer=manufacturer,
            tenant=cls.owner,
        )
        cls.stock = AccessoryStock.objects.create(
            accessory=cls.accessory,
            location=cls.location,
            qty=10,
        )

        # Grantee-side technician holding PERM in the grantee tenant.
        cls.tech = User.objects.create_user(username="ra-tech", password="x")
        role = Role.objects.create(
            tenant=cls.grantee,
            name="RA Tech",
            permissions=[PERM],
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
        if kwargs["resource_type"] is None:
            kwargs["resource_type"] = ContentType.objects.get_for_model(AccessoryStock)
        return TenantResourceGrant.objects.create(**kwargs)

    # ------------------------------------------------------------- same tenant
    def test_same_tenant_rbac_only(self):
        owner_role = Role.objects.create(
            tenant=self.owner,
            name="RA Owner Role",
            permissions=[PERM],
        )
        owner_user = User.objects.create_user(username="ra-owner-user", password="x")
        self.grant(owner_user, self.owner, owner_role)
        decision = resolve_stock_access(
            owner_user,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.owner,
        )
        assert decision.allowed
        assert decision.reason == REASON_SAME_TENANT
        assert decision.grant is None

    def test_same_tenant_without_perm_denied(self):
        nobody = User.objects.create_user(username="ra-nobody", password="x")
        decision = resolve_stock_access(
            nobody,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.owner,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_RBAC

    # ------------------------------------------------------------ cross tenant
    def test_no_grant_denied(self):
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_public_resolver_rejects_caller_supplied_batch_evidence(self):
        with self.assertRaises(TypeError):
            resolve_stock_access(
                self.tech,
                self.stock,
                TenantResourceGrant.ACCESS_USE,
                PERM,
                active_tenant=self.grantee,
                _evidence=object(),
            )

    def test_cyclic_group_topology_fails_closed(self):
        self._use_grant(
            grantee_tenant=None,
            grantee_tenant_group=self.root_group,
        )
        TenantGroup._base_manager.filter(pk=self.root_group.pk).update(parent_id=self.child_group.pk)

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_direct_grant_allows_and_returns_grant(self):
        grant_row = self._use_grant()
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed
        assert decision.reason == REASON_DIRECT_GRANT
        assert decision.grant == grant_row
        assert decision.owner_tenant_id == self.owner.pk

    def test_shared_stock_batch_query_count_does_not_scale_per_stock(self):
        parent = self.child_group
        for index in range(8):
            parent = TenantGroup.objects.create(
                name=f"RA Batch Depth {index}",
                slug=f"ra-batch-depth-{index}",
                parent=parent,
            )
        self.grantee.group = parent
        self.grantee.save(update_fields=["group"])

        stock_ids = []
        for index in range(12):
            accessory = Accessory.objects.create(
                name=f"RA Batch Dock {index}",
                slug=f"ra-batch-dock-{index}",
                manufacturer=self.accessory.manufacturer,
                tenant=self.owner,
            )
            stock = AccessoryStock.objects.create(
                accessory=accessory,
                location=self.location,
                qty=1,
            )
            stock_ids.append(stock.pk)
            self._use_grant(resource_id=stock.pk)

        with CaptureQueriesContext(connection) as queries:
            resolved_ids = resolved_shared_stock_ids(
                AccessoryStock,
                self.grantee,
                self.tech,
                TenantResourceGrant.ACCESS_USE,
                PERM,
            )

        self.assertEqual(set(resolved_ids), set(stock_ids))
        self.assertLessEqual(len(queries), 12)

    def test_view_grant_does_not_cover_use(self):
        self._use_grant(access_level=TenantResourceGrant.ACCESS_VIEW)
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_INSUFFICIENT_LEVEL

    def test_use_grant_covers_view(self):
        self._use_grant()
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_VIEW,
            PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed

    def test_revoked_grant_denied(self):
        grant_row = self._use_grant()
        grant_row.delete()
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_grant_without_rbac_denied(self):
        self._use_grant()
        nobody = User.objects.create_user(username="ra-nobody2", password="x")
        decision = resolve_stock_access(
            nobody,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_RBAC
        assert decision.grant is not None  # the grant existed; the USER failed

    def test_grant_without_actor_denied(self):
        self._use_grant()

        decision = resolve_stock_access(
            None,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == DENIED_RBAC

    def test_trusted_system_context_allows_exact_tenant_and_permission(self):
        grant_row = self._use_grant()

        with TaskContext(tenant_id=self.grantee.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=PERM,
                operation="inventory.checkout",
                reason="Reconcile an approved stock allocation",
            )
            decision = resolve_stock_access(
                None,
                self.stock,
                TenantResourceGrant.ACCESS_USE,
                PERM,
                active_tenant=self.grantee,
                system_authorization=authorization,
                system_operation="inventory.checkout",
            )

        assert decision.allowed
        assert decision.grant == grant_row
        assert decision.system_authorization == authorization

    def test_trusted_system_context_cannot_be_constructed_directly(self):
        with self.assertRaises(TypeError):
            SystemAuthorizationContext(
                tenant_id=self.grantee.pk,
                permission=PERM,
                operation="inventory.checkout",
                reason="Forged outside TaskContext",
                request_id="forged",
            )

    def test_private_factory_and_cloned_context_cannot_forge_issuance(self):
        self._use_grant()
        with TaskContext(tenant_id=self.grantee.pk, user_id=None) as task_context:
            issued = task_context.authorize_system(
                permission=PERM,
                operation="inventory.checkout",
                reason="Approved inventory reconciliation",
            )
            with self.assertRaises(PermissionError):
                _issue_system_authorization(
                    tenant_id=self.grantee.pk,
                    permission=PERM,
                    operation="inventory.checkout",
                    reason="Forged private-factory call",
                    request_id=issued.request_id,
                    issuer=object(),
                )

            forged = object.__new__(SystemAuthorizationContext)
            for name in (
                "tenant_id",
                "permission",
                "operation",
                "reason",
                "request_id",
                "_issuer",
            ):
                object.__setattr__(forged, name, getattr(issued, name))
            decision = resolve_stock_access(
                None,
                self.stock,
                TenantResourceGrant.ACCESS_USE,
                PERM,
                active_tenant=self.grantee,
                system_authorization=forged,
                system_operation="inventory.checkout",
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DENIED_RBAC)

    def test_trusted_system_context_does_not_authorize_a_different_operation(self):
        self._use_grant()

        with TaskContext(tenant_id=self.grantee.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=PERM,
                operation="inventory.checkout",
                reason="Reconcile an approved stock allocation",
            )
            decision = resolve_stock_access(
                None,
                self.stock,
                TenantResourceGrant.ACCESS_USE,
                PERM,
                active_tenant=self.grantee,
                system_authorization=authorization,
                system_operation="inventory.export",
            )

        assert not decision.allowed
        assert decision.reason == DENIED_RBAC

    def test_trusted_system_context_does_not_survive_active_tenant_switch(self):
        self._use_grant()

        with TaskContext(tenant_id=self.grantee.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=PERM,
                operation="inventory.checkout",
                reason="Reconcile an approved stock allocation",
            )
            set_current_tenant(self.third)
            decision = resolve_stock_access(
                None,
                self.stock,
                TenantResourceGrant.ACCESS_USE,
                PERM,
                active_tenant=self.grantee,
                system_authorization=authorization,
                system_operation="inventory.checkout",
            )

        assert not decision.allowed
        assert decision.reason == DENIED_RBAC

    def test_trusted_system_context_cannot_be_created_outside_entered_context(self):
        task_context = TaskContext(tenant_id=self.grantee.pk, user_id=None)

        with self.assertRaises(PermissionDenied):
            task_context.authorize_system(
                permission=PERM,
                operation="inventory.checkout",
                reason="Not inside the declared task scope",
            )

    def test_actor_bound_task_cannot_create_trusted_system_context(self):
        with TaskContext(tenant_id=self.grantee.pk, user_id=self.tech.pk) as task_context:
            with self.assertRaises(PermissionDenied):
                task_context.authorize_system(
                    permission=PERM,
                    operation="inventory.checkout",
                    reason="Actor-bound work must use normal RBAC",
                )

    def test_trusted_system_context_does_not_authorize_a_different_permission(self):
        self._use_grant()

        with TaskContext(tenant_id=self.grantee.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=PERM,
                operation="inventory.checkout",
                reason="Reconcile an approved stock allocation",
            )
            decision = resolve_stock_access(
                None,
                self.stock,
                TenantResourceGrant.ACCESS_USE,
                "inventory.delete_accessoryassignment",
                active_tenant=self.grantee,
                system_authorization=authorization,
                system_operation="inventory.checkout",
            )

        assert not decision.allowed
        assert decision.reason == DENIED_RBAC

    def test_direct_grant_becomes_inert_when_owner_is_deleted(self):
        self._use_grant()
        # PROTECT prevents this state through normal application writes. Simulate
        # a stale/imported row to prove the resolver still fails closed.
        Tenant.objects.filter(pk=self.owner.pk).update(deleted_at=timezone.now())

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == DENIED_OWNER_UNRESOLVABLE

    def test_direct_grant_becomes_inert_when_grantee_is_deleted(self):
        self._use_grant()
        self.grantee.delete()

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == DENIED_NO_ACTIVE_TENANT

    def test_group_grant_becomes_inert_when_active_tenant_is_deleted(self):
        self._use_grant(grantee_tenant=None, grantee_tenant_group=self.root_group)
        superuser = User.objects.create_superuser(
            username="ra-deleted-active-superuser",
            password="x",
            email="deleted-active@example.invalid",
        )
        self.grantee.delete()

        decision = resolve_stock_access(
            superuser,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == DENIED_NO_ACTIVE_TENANT

    def test_approved_stock_uses_persisted_location_when_instance_is_malformed(self):
        malformed_stock = AccessoryStock(
            accessory=self.accessory,
            location=None,
            qty=1,
        )
        malformed_stock.pk = self.stock.pk

        decision = resolve_stock_access(
            self.tech,
            malformed_stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_scalar_resolver_ignores_in_memory_stock_location_spoof(self):
        grantee_site = Site.objects.create(
            name="Grantee Site",
            slug="grantee-site-scalar-spoof",
            tenant=self.grantee,
        )
        grantee_location = Location.objects.create(
            name="Grantee Location",
            slug="grantee-location-scalar-spoof",
            site=grantee_site,
            tenant=self.grantee,
        )
        self.stock.location = grantee_location

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DENIED_NO_GRANT)
        self.assertEqual(decision.owner_tenant_id, self.owner.pk)

    def test_scalar_resolver_uses_current_persisted_active_group(self):
        self._use_grant(
            grantee_tenant=None,
            grantee_tenant_group=self.root_group,
        )
        unrelated_group = TenantGroup.objects.create(
            name="Reassigned Group",
            slug="reassigned-group-scalar",
        )
        Tenant._base_manager.filter(pk=self.grantee.pk).update(group=unrelated_group)

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DENIED_NO_GRANT)

    def test_out_of_allowlist_resource_type_denied(self):
        class RogueStock:
            _meta = Accessory._meta

        rogue = RogueStock()
        rogue.pk = self.accessory.pk
        rogue.location = self.location
        grant_row = self._use_grant()
        # Model validation rejects this through normal writes. Bypass signals to
        # prove a malformed/imported row cannot become an authorization bypass.
        TenantResourceGrant.objects.filter(pk=grant_row.pk).update(
            resource_type=ContentType.objects.get_for_model(Accessory),
            resource_id=rogue.pk,
        )

        decision = resolve_stock_access(
            self.tech,
            rogue,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == "unsupported-resource-type"

    def test_invalid_stored_access_level_denied_instead_of_raising(self):
        grant_row = self._use_grant()
        TenantResourceGrant.objects.filter(pk=grant_row.pk).update(access_level="rogue")

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_VIEW,
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == "invalid-access-level"

    def test_invalid_requested_access_level_denied_instead_of_raising(self):
        self._use_grant()

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            "rogue",
            PERM,
            active_tenant=self.grantee,
        )

        assert not decision.allowed
        assert decision.reason == "invalid-access-level"

    # ------------------------------------------------------------ group grants
    def test_group_grant_covers_descendant_group_tenant(self):
        grant_row = self._use_grant(
            grantee_tenant=None,
            grantee_tenant_group=self.root_group,
        )
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,  # group chain: child -> root
        )
        assert decision.allowed
        assert decision.reason == REASON_GROUP_GRANT
        assert decision.grant == grant_row

    def test_direct_grant_wins_over_ancestor_grant_and_returns_exact_grant(self):
        ancestor_grant = self._use_grant(
            grantee_tenant=None,
            grantee_tenant_group=self.root_group,
        )
        direct_grant = self._use_grant()

        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )

        assert decision.allowed
        assert decision.reason == REASON_DIRECT_GRANT
        assert decision.grant == direct_grant
        assert decision.grant != ancestor_grant

    def test_group_grant_becomes_inert_when_ancestor_group_is_deleted(self):
        middle_group = TenantGroup.objects.create(
            name="RA Middle",
            slug="ra-middle",
            parent=self.root_group,
        )
        self.child_group.parent = middle_group
        self.child_group.save(update_fields=["parent"])
        grant_row = self._use_grant(
            grantee_tenant=None,
            grantee_tenant_group=self.root_group,
        )
        before = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert before.allowed

        middle_group.delete()

        grant_row.refresh_from_db()
        assert grant_row.deleted_at is None
        after = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not after.allowed
        assert after.reason == DENIED_NO_GRANT

    def test_group_grant_on_own_group(self):
        self._use_grant(grantee_tenant=None, grantee_tenant_group=self.child_group)
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed

    def test_group_grant_on_sibling_group_denied(self):
        self._use_grant(grantee_tenant=None, grantee_tenant_group=self.sibling_group)
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_tenant_without_group_ignores_group_grants(self):
        self._use_grant(grantee_tenant=None, grantee_tenant_group=self.root_group)
        role = Role.objects.create(tenant=self.third, name="RA T3", permissions=[PERM])
        user3 = User.objects.create_user(username="ra-user3", password="x")
        self.grant(user3, self.third, role)
        decision = resolve_stock_access(
            user3,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
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
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.third,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT

    def test_superuser_needs_grant_for_cross_tenant(self):
        boss = User.objects.create_superuser(username="ra-boss", password="x")
        decision = resolve_stock_access(
            boss,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_GRANT
        self._use_grant()
        decision = resolve_stock_access(
            boss,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert decision.allowed

    def test_no_active_tenant_denied(self):
        self.clear_tenant_context()
        decision = resolve_stock_access(
            self.tech,
            self.stock,
            TenantResourceGrant.ACCESS_USE,
            PERM,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_NO_ACTIVE_TENANT

    def test_owner_unresolvable_denied(self):
        # ADR-0001 phase 4: AbstractStock now derives+requires a tenant from
        # its location at creation time, so a pool can no longer be created
        # directly at a tenant-less location. Reproduce an unresolvable owner
        # by clearing the location's tenant AFTER the stock already exists
        # (e.g. tenant offboarding leaving a stray pool behind).
        site = Site.objects.create(name="RA NoT Site", slug="ra-not-site")
        loc = Location.objects.create(
            name="RA NoT",
            slug="ra-not",
            site=site,
            tenant=self.owner,
        )
        stray = AccessoryStock.objects.create(
            accessory=self.accessory,
            location=loc,
            qty=1,
        )
        loc.tenant = None
        loc.save()
        decision = resolve_stock_access(
            self.tech,
            stray,
            TenantResourceGrant.ACCESS_USE,
            PERM,
            active_tenant=self.grantee,
        )
        assert not decision.allowed
        assert decision.reason == DENIED_OWNER_UNRESOLVABLE

    def test_active_tenant_defaults_to_context(self):
        self._use_grant()
        with self.tenant_context(self.grantee):
            decision = resolve_stock_access(
                self.tech,
                self.stock,
                TenantResourceGrant.ACCESS_USE,
                PERM,
            )
        assert decision.allowed
