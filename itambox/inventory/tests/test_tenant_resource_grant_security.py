"""Frozen adversarial boundary for tenant resource grants (#194).

Every approved stock family must preserve the same grant, tenant, RBAC,
surface, and provenance semantics. Keep this file in the mandatory selector
when changing the resolver or any shared-stock surface.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assets.models import Category, Manufacturer
from core.tasks.context import TaskContext
from core.tasks.resource_grants import sweep_expired_resource_grants
from core.tests.mixins import TenantTestMixin
from inventory.forms import AccessoryCheckoutForm, ComponentCheckoutForm, ConsumableCheckoutForm
from inventory.models import (
    Accessory,
    AccessoryStock,
    Component,
    ComponentStock,
    Consumable,
    ConsumableStock,
)
from inventory.services import checkout_inventory_item
from organization.models import (
    AssetHolder,
    Location,
    Role,
    Site,
    Tenant,
    TenantGroup,
    TenantResourceGrant,
    TenantResourceGrantExpiryRun,
)
from organization.services import (
    DENIED_INSUFFICIENT_LEVEL,
    DENIED_NO_ACTIVE_TENANT,
    DENIED_NO_GRANT,
    DENIED_OWNER_UNRESOLVABLE,
    DENIED_RBAC,
    REASON_DIRECT_GRANT,
    REASON_GROUP_GRANT,
    resolve_stock_access,
)

User = get_user_model()


class TenantResourceGrantSecurityBoundaryTests(TenantTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_root = TenantGroup.objects.create(name="TRGS Root", slug="trgs-root")
        cls.group_child = TenantGroup.objects.create(
            name="TRGS Child",
            slug="trgs-child",
            parent=cls.group_root,
        )
        cls.owner = Tenant.objects.create(name="TRGS Owner", slug="trgs-owner")
        cls.grantee = Tenant.objects.create(
            name="TRGS Grantee",
            slug="trgs-grantee",
            group=cls.group_child,
        )
        cls.unrelated = Tenant.objects.create(name="TRGS Unrelated", slug="trgs-unrelated")

        cls.owner_site = Site.objects.create(name="TRGS Owner Site", slug="trgs-owner-site", tenant=cls.owner)
        cls.grantee_site = Site.objects.create(
            name="TRGS Grantee Site",
            slug="trgs-grantee-site",
            tenant=cls.grantee,
        )
        cls.owner_location = Location.objects.create(
            name="TRGS Owner Depot",
            slug="trgs-owner-depot",
            site=cls.owner_site,
            tenant=cls.owner,
        )
        cls.grantee_location = Location.objects.create(
            name="TRGS Grantee Depot",
            slug="trgs-grantee-depot",
            site=cls.grantee_site,
            tenant=cls.grantee,
        )
        cls.holder = AssetHolder.objects.create(
            first_name="TRGS",
            last_name="Holder",
            upn="trgs-holder@example.invalid",
            tenant=cls.grantee,
        )

        manufacturer = Manufacturer.objects.create(name="TRGS Manufacturer", slug="trgs-manufacturer")
        definitions = (
            {
                "name": "accessory",
                "item_model": Accessory,
                "stock_model": AccessoryStock,
                "item_field": "accessory",
                "form": AccessoryCheckoutForm,
                "assignment_perm": "inventory.add_accessoryassignment",
                "list_url": "inventory:accessorystock_list",
                "api_name": "accessorystock",
            },
            {
                "name": "component",
                "item_model": Component,
                "stock_model": ComponentStock,
                "item_field": "component",
                "form": ComponentCheckoutForm,
                "assignment_perm": "inventory.add_componentallocation",
                "list_url": "inventory:componentstock_list",
                "api_name": "componentstock",
            },
            {
                "name": "consumable",
                "item_model": Consumable,
                "stock_model": ConsumableStock,
                "item_field": "consumable",
                "form": ConsumableCheckoutForm,
                "assignment_perm": "inventory.add_consumableassignment",
                "list_url": "inventory:consumablestock_list",
                "api_name": "consumablestock",
            },
        )
        cls.families = []
        for definition in definitions:
            name = definition["name"]
            category = Category.objects.create(
                name=f"TRGS {name.title()}",
                slug=f"trgs-{name}",
                applies_to={name: True},
            )
            item = definition["item_model"].objects.create(
                name=f"TRGS Owner {name.title()}",
                manufacturer=manufacturer,
                category=category,
                tenant=cls.owner,
            )
            stock = definition["stock_model"].objects.create(
                **{definition["item_field"]: item},
                location=cls.owner_location,
                qty=10,
            )
            reverse_item = definition["item_model"].objects.create(
                name=f"TRGS Grantee {name.title()}",
                manufacturer=manufacturer,
                category=category,
                tenant=cls.grantee,
            )
            reverse_stock = definition["stock_model"].objects.create(
                **{definition["item_field"]: reverse_item},
                location=cls.grantee_location,
                qty=10,
            )
            cls.families.append(
                {
                    **definition,
                    "item": item,
                    "stock": stock,
                    "reverse_stock": reverse_stock,
                    "view_perm": f"inventory.view_{definition['stock_model']._meta.model_name}",
                }
            )

    def _actor(self, tenant, username, permissions):
        user = User.objects.create_user(username=username, password="x")
        role = Role.objects.create(
            tenant=tenant,
            name=f"TRGS {username}",
            permissions=permissions,
        )
        self.grant(user, tenant, role)
        return user

    def _grant(self, family, *, access=TenantResourceGrant.ACCESS_USE, direct=True):
        return TenantResourceGrant.objects.create(
            tenant=self.owner,
            grantee_tenant=self.grantee if direct else None,
            grantee_tenant_group=None if direct else self.group_root,
            resource_type=ContentType.objects.get_for_model(family["stock_model"]),
            resource_id=family["stock"].pk,
            access_level=access,
        )

    def _resolve(self, actor, family, *, active_tenant=None, access=TenantResourceGrant.ACCESS_USE):
        return resolve_stock_access(
            actor,
            family["stock"],
            access,
            family["assignment_perm"],
            active_tenant=active_tenant or self.grantee,
        )

    def test_direct_grant_and_exact_provenance_cover_every_stock_family(self):
        actor = self._actor(
            self.grantee,
            "trgs-direct",
            [family["assignment_perm"] for family in self.families],
        )
        for family in self.families:
            with self.subTest(family=family["name"]):
                grant = self._grant(family)
                decision = self._resolve(actor, family)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.reason, REASON_DIRECT_GRANT)
                self.assertEqual(decision.grant.pk, grant.pk)

    def test_ancestor_group_grant_and_exact_provenance_cover_every_stock_family(self):
        actor = self._actor(
            self.grantee,
            "trgs-group",
            [family["assignment_perm"] for family in self.families],
        )
        for family in self.families:
            with self.subTest(family=family["name"]):
                grant = self._grant(family, direct=False)
                decision = self._resolve(actor, family)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.reason, REASON_GROUP_GRANT)
                self.assertEqual(decision.grant.pk, grant.pk)

    def test_direct_grant_wins_over_ancestor_group_grant_for_every_family(self):
        actor = self._actor(
            self.grantee,
            "trgs-precedence",
            [family["assignment_perm"] for family in self.families],
        )
        for family in self.families:
            with self.subTest(family=family["name"]):
                self._grant(family, direct=False)
                direct = self._grant(family)
                decision = self._resolve(actor, family)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.grant.pk, direct.pk)

    def test_level_rbac_revocation_and_superuser_fail_closed_for_every_family(self):
        actor = self._actor(
            self.grantee,
            "trgs-levels",
            [family["assignment_perm"] for family in self.families],
        )
        no_rbac = self._actor(self.grantee, "trgs-no-rbac", [])
        superuser = User.objects.create_superuser(username="trgs-super", password="x", email="trgs@example.invalid")
        for family in self.families:
            with self.subTest(family=family["name"], case="level"):
                view_grant = self._grant(family, access=TenantResourceGrant.ACCESS_VIEW)
                self.assertEqual(self._resolve(actor, family).reason, DENIED_INSUFFICIENT_LEVEL)
                view_grant.delete()
            with self.subTest(family=family["name"], case="rbac"):
                use_grant = self._grant(family)
                self.assertEqual(self._resolve(no_rbac, family).reason, DENIED_RBAC)
                use_grant.delete()
            with self.subTest(family=family["name"], case="revoked"):
                revoked = self._grant(family)
                revoked.delete()
                self.assertEqual(self._resolve(actor, family).reason, DENIED_NO_GRANT)
            with self.subTest(family=family["name"], case="superuser-no-grant"):
                self.assertEqual(self._resolve(superuser, family).reason, DENIED_NO_GRANT)

    def test_s13_expiry_removes_direct_and_group_access_even_for_superuser(self):
        actor = self._actor(
            self.grantee,
            "trgs-expiry-access",
            [family["assignment_perm"] for family in self.families],
        )
        superuser = User.objects.create_superuser(
            username="trgs-expiry-superuser",
            password="x",
            email="trgs-expiry-superuser@example.invalid",
        )
        cutoff = timezone.now()
        grants = [self._grant(family, access=TenantResourceGrant.ACCESS_USE) for family in self.families]
        for family in self.families:
            group_grant = self._grant(family, access=TenantResourceGrant.ACCESS_VIEW, direct=False)
            grants.append(group_grant)
        TenantResourceGrant._base_manager.filter(pk__in=[grant.pk for grant in grants]).update(valid_until=cutoff)
        run = TenantResourceGrantExpiryRun._base_manager.create(
            tenant=self.owner,
            schedule_slot=cutoff.replace(minute=0, second=0, microsecond=0),
            cutoff=cutoff,
            dispatch_stale_at=cutoff + timezone.timedelta(minutes=1),
        )
        sweep_expired_resource_grants(self.owner.pk, run.pk, 1)
        for family in self.families:
            with self.subTest(family=family["name"], actor="grantee"):
                self.assertEqual(self._resolve(actor, family).reason, DENIED_NO_GRANT)
            with self.subTest(family=family["name"], actor="superuser"):
                self.assertEqual(self._resolve(superuser, family).reason, DENIED_NO_GRANT)

    def test_s14_expiry_preserves_assignment_provenance(self):
        permissions = [family["assignment_perm"] for family in self.families]
        actor = self._actor(self.grantee, "trgs-expiry-provenance", permissions)
        grants = {family["name"]: self._grant(family) for family in self.families}
        assignments = {}
        with self.tenant_context(self.grantee):
            for family in self.families:
                assignments[family["name"]] = checkout_inventory_item(
                    family["item"],
                    1,
                    holder=self.holder,
                    source_location=self.owner_location,
                    user=actor,
                )
        cutoff = timezone.now()
        TenantResourceGrant._base_manager.filter(pk__in=[grant.pk for grant in grants.values()]).update(
            valid_until=cutoff
        )
        run = TenantResourceGrantExpiryRun._base_manager.create(
            tenant=self.owner,
            schedule_slot=cutoff.replace(minute=0, second=0, microsecond=0),
            cutoff=cutoff,
            dispatch_stale_at=cutoff + timezone.timedelta(minutes=1),
        )
        sweep_expired_resource_grants(self.owner.pk, run.pk, 1)
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = assignments[family["name"]].__class__._base_manager.get(pk=assignments[family["name"]].pk)
                self.assertEqual(assignment.resource_grant_id, grants[family["name"]].pk)
                revoked_grant = TenantResourceGrant._base_manager.get(pk=assignment.resource_grant_id)
                self.assertIsNotNone(revoked_grant.deleted_at)

    def test_actorless_calls_are_denied_for_every_stock_family(self):
        for family in self.families:
            self._grant(family)
            with self.subTest(family=family["name"]):
                self.assertEqual(self._resolve(None, family).reason, DENIED_RBAC)

    def test_view_and_use_levels_both_satisfy_read_for_every_stock_family(self):
        actor = self._actor(
            self.grantee,
            "trgs-read-levels",
            [family["assignment_perm"] for family in self.families],
        )
        for family in self.families:
            with self.subTest(family=family["name"], grant="view"):
                grant = self._grant(family, access=TenantResourceGrant.ACCESS_VIEW)
                self.assertTrue(self._resolve(actor, family, access=TenantResourceGrant.ACCESS_VIEW).allowed)
                grant.delete()
            with self.subTest(family=family["name"], grant="use"):
                grant = self._grant(family)
                self.assertTrue(self._resolve(actor, family, access=TenantResourceGrant.ACCESS_VIEW).allowed)
                grant.delete()

    def test_same_tenant_rbac_is_required_for_every_stock_family(self):
        actor = self._actor(
            self.owner,
            "trgs-owner-rbac",
            [family["assignment_perm"] for family in self.families],
        )
        denied_actor = self._actor(self.owner, "trgs-owner-no-rbac", [])
        for family in self.families:
            with self.subTest(family=family["name"], rbac="present"):
                self.assertTrue(self._resolve(actor, family, active_tenant=self.owner).allowed)
            with self.subTest(family=family["name"], rbac="missing"):
                self.assertEqual(
                    self._resolve(denied_actor, family, active_tenant=self.owner).reason,
                    DENIED_RBAC,
                )

    def test_issued_actorless_authorization_succeeds_for_every_stock_family(self):
        for family in self.families:
            self._grant(family)
            with (
                self.subTest(family=family["name"]),
                TaskContext(
                    tenant_id=self.grantee.pk,
                ) as task,
            ):
                authorization = task.authorize_system(
                    permission=family["assignment_perm"],
                    operation="checkout_inventory_item",
                    reason="TRGS issued actorless matrix",
                )
                decision = resolve_stock_access(
                    None,
                    family["stock"],
                    TenantResourceGrant.ACCESS_USE,
                    family["assignment_perm"],
                    active_tenant=self.grantee,
                    system_authorization=authorization,
                    system_operation="checkout_inventory_item",
                )
                self.assertTrue(decision.allowed)

    def test_malformed_grant_owner_resource_and_content_type_fail_closed_for_every_family(self):
        actor = self._actor(
            self.grantee,
            "trgs-malformed",
            [family["assignment_perm"] for family in self.families],
        )
        tenant_type = ContentType.objects.get_for_model(Tenant)
        for family in self.families:
            malformed = (
                {
                    "tenant": self.unrelated,
                    "resource_type": ContentType.objects.get_for_model(family["stock_model"]),
                    "resource_id": family["stock"].pk,
                },
                {
                    "tenant": self.owner,
                    "resource_type": ContentType.objects.get_for_model(family["stock_model"]),
                    "resource_id": family["stock"].pk + 10_000_000,
                },
                {
                    "tenant": self.owner,
                    "resource_type": tenant_type,
                    "resource_id": family["stock"].pk,
                },
            )
            for case, fields in enumerate(malformed):
                with self.subTest(family=family["name"], malformed=case):
                    grant = TenantResourceGrant(
                        grantee_tenant=self.grantee,
                        access_level=TenantResourceGrant.ACCESS_USE,
                        **fields,
                    )
                    TenantResourceGrant._base_manager.bulk_create([grant])
                    self.assertEqual(self._resolve(actor, family).reason, DENIED_NO_GRANT)
                    TenantResourceGrant._base_manager.filter(pk=grant.pk).delete()

    def test_stale_grantee_and_group_fail_closed_for_every_family(self):
        actor = User.objects.create_superuser(
            username="trgs-stale-super",
            password="x",
            email="trgs-stale@example.invalid",
        )
        for family in self.families:
            self._grant(family, direct=False)
        TenantGroup._base_manager.filter(pk=self.group_child.pk).update(deleted_at=timezone.now())
        for family in self.families:
            with self.subTest(family=family["name"], stale="group"):
                self.assertEqual(self._resolve(actor, family).reason, DENIED_NO_GRANT)

        Tenant._base_manager.filter(pk=self.grantee.pk).update(deleted_at=timezone.now())
        for family in self.families:
            with self.subTest(family=family["name"], stale="grantee"):
                self.assertEqual(
                    self._resolve(actor, family).reason,
                    DENIED_NO_ACTIVE_TENANT,
                )

    def test_stale_owner_location_fails_closed_for_every_family(self):
        actor = User.objects.create_superuser(
            username="trgs-stale-location",
            password="x",
            email="trgs-stale-location@example.invalid",
        )
        for family in self.families:
            self._grant(family)
        Location._base_manager.filter(pk=self.owner_location.pk).update(deleted_at=timezone.now())
        for family in self.families:
            with self.subTest(family=family["name"], stale="owner-location"):
                self.assertEqual(
                    self._resolve(actor, family).reason,
                    DENIED_OWNER_UNRESOLVABLE,
                )

    def test_stale_owner_tenant_fails_closed_for_every_family(self):
        actor = User.objects.create_superuser(
            username="trgs-stale-owner",
            password="x",
            email="trgs-stale-owner@example.invalid",
        )
        for family in self.families:
            self._grant(family)
        Tenant._base_manager.filter(pk=self.owner.pk).update(deleted_at=timezone.now())
        for family in self.families:
            with self.subTest(family=family["name"], stale="owner"):
                self.assertEqual(
                    self._resolve(actor, family).reason,
                    DENIED_OWNER_UNRESOLVABLE,
                )

    def test_unrelated_and_reverse_direction_tenants_are_denied_for_every_family(self):
        unrelated_actor = self._actor(
            self.unrelated,
            "trgs-unrelated",
            [family["assignment_perm"] for family in self.families],
        )
        owner_actor = self._actor(
            self.owner,
            "trgs-owner-actor",
            [family["assignment_perm"] for family in self.families],
        )
        for family in self.families:
            with self.subTest(family=family["name"], case="unrelated"):
                self._grant(family)
                self.assertEqual(
                    self._resolve(unrelated_actor, family, active_tenant=self.unrelated).reason,
                    DENIED_NO_GRANT,
                )
            with self.subTest(family=family["name"], case="reverse"):
                decision = resolve_stock_access(
                    owner_actor,
                    family["reverse_stock"],
                    TenantResourceGrant.ACCESS_USE,
                    family["assignment_perm"],
                    active_tenant=self.owner,
                )
                self.assertEqual(decision.reason, DENIED_NO_GRANT)

    def test_grants_are_non_transitive_across_a_to_b_to_c_chain_for_every_family(self):
        unrelated_actor = self._actor(
            self.unrelated,
            "trgs-chain-c",
            [family["assignment_perm"] for family in self.families],
        )
        for family in self.families:
            self._grant(family)  # owner A -> grantee B
            TenantResourceGrant._base_manager.bulk_create(
                [
                    TenantResourceGrant(
                        tenant=self.grantee,
                        grantee_tenant=self.unrelated,
                        resource_type=ContentType.objects.get_for_model(family["stock_model"]),
                        resource_id=family["stock"].pk,  # attempted B -> C re-share of A's resource
                        access_level=TenantResourceGrant.ACCESS_USE,
                    )
                ]
            )
            with self.subTest(family=family["name"]):
                decision = self._resolve(
                    unrelated_actor,
                    family,
                    active_tenant=self.unrelated,
                )
                self.assertEqual(decision.reason, DENIED_NO_GRANT)

    def test_ui_rest_forms_and_checkout_share_the_boundary_for_every_family(self):
        permissions = [
            permission for family in self.families for permission in (family["view_perm"], family["assignment_perm"])
        ]
        actor = self._actor(self.grantee, "trgs-surfaces", permissions)
        grants = {family["name"]: self._grant(family) for family in self.families}
        self.client_login_to_tenant(actor, self.grantee, role_permissions=permissions)

        for family in self.families:
            with self.subTest(family=family["name"], surface="ui-list"):
                response = self.client.get(reverse(family["list_url"]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, self.owner_location.name)
            with self.subTest(family=family["name"], surface="rest-detail"):
                url = reverse(
                    f"api:inventory_api:{family['api_name']}-detail",
                    kwargs={"pk": family["stock"].pk},
                )
                self.assertEqual(self.client.get(url).status_code, 200)
            with self.subTest(family=family["name"], surface="form-source"):
                with self.tenant_context(self.grantee):
                    form = family["form"](**{family["name"]: family["item"]}, user=actor)
                self.assertIn(
                    self.owner_location.pk,
                    form.fields["from_location"].queryset.values_list("pk", flat=True),
                )
            with self.subTest(family=family["name"], surface="checkout"):
                with self.tenant_context(self.grantee):
                    assignment = checkout_inventory_item(
                        family["item"],
                        1,
                        holder=self.holder,
                        source_location=self.owner_location,
                        user=actor,
                    )
                self.assertEqual(assignment.resource_grant_id, grants[family["name"]].pk)

    def test_hostile_direct_rest_ids_are_concealed_for_every_family(self):
        permissions = [family["view_perm"] for family in self.families]
        actor = self._actor(self.unrelated, "trgs-hostile-rest", permissions)
        self.client_login_to_tenant(actor, self.unrelated, role_permissions=permissions)
        for family in self.families:
            self._grant(family)
            with self.subTest(family=family["name"]):
                url = reverse(
                    f"api:inventory_api:{family['api_name']}-detail",
                    kwargs={"pk": family["stock"].pk},
                )
                self.assertEqual(self.client.get(url).status_code, 404)
