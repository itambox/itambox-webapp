"""Immutable system-authorization provenance on inventory assignments (#194)."""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from assets.models import Category, Manufacturer
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin, grant
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    AccessoryStock,
    Component,
    ComponentAllocation,
    ComponentStock,
    Consumable,
    ConsumableAssignment,
    ConsumableStock,
)
from inventory.models_assignment_write import authorized_assignment_write
from inventory.services import (
    CHECKOUT_OPERATION,
    COMPONENT_ALLOCATION_OPERATION,
    checkout_inventory_item,
    create_component_allocation,
    update_component_allocation,
)
from organization.models import AssetHolder, Location, Role, Site, Tenant, TenantResourceGrant

User = get_user_model()
REASON = "Nightly trusted-system inventory reconciliation"


class AssignmentSystemAuthorizationProvenanceTests(TenantTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Provenance Tenant", slug="provenance-tenant")
        site = Site.objects.create(name="Provenance Site", slug="provenance-site", tenant=cls.tenant)
        cls.location = Location.objects.create(
            name="Provenance Store",
            slug="provenance-store",
            site=site,
            tenant=cls.tenant,
        )
        cls.holder = AssetHolder.objects.create(
            first_name="System",
            last_name="Target",
            upn="system-target@example.invalid",
            tenant=cls.tenant,
        )
        manufacturer = Manufacturer.objects.create(name="Provenance Manufacturer", slug="provenance-manufacturer")
        definitions = (
            ("accessory", Accessory, AccessoryStock, AccessoryAssignment, "inventory.add_accessoryassignment"),
            ("component", Component, ComponentStock, ComponentAllocation, "inventory.add_componentallocation"),
            ("consumable", Consumable, ConsumableStock, ConsumableAssignment, "inventory.add_consumableassignment"),
        )
        cls.families = []
        for name, item_model, stock_model, assignment_model, permission in definitions:
            category = Category.objects.create(
                name=f"Provenance {name.title()}",
                slug=f"provenance-{name}",
                applies_to={name: True},
            )
            item = item_model.objects.create(
                name=f"Provenance {name.title()}",
                manufacturer=manufacturer,
                category=category,
                tenant=cls.tenant,
            )
            stock_model.objects.create(**{name: item}, location=cls.location, qty=20)
            cls.families.append(
                {
                    "name": name,
                    "item": item,
                    "assignment_model": assignment_model,
                    "permission": permission,
                }
            )
        cls.actor = User.objects.create_user(username="provenance-human", password="x")
        role = Role.objects.create(
            tenant=cls.tenant,
            name="Provenance operator",
            permissions=[family["permission"] for family in cls.families],
        )
        grant(cls.actor, cls.tenant, role)

    def _actorless_assignments(self):
        assignments = []
        for family in self.families:
            with TaskContext(tenant_id=self.tenant.pk, user_id=None) as task_context:
                authorization = task_context.authorize_system(
                    permission=family["permission"],
                    operation=CHECKOUT_OPERATION,
                    reason=REASON,
                )
                assignment = checkout_inventory_item(
                    family["item"],
                    1,
                    holder=self.holder,
                    source_location=self.location,
                    system_authorization=authorization,
                )
            assignments.append((family, assignment))
        return assignments

    def test_actorless_exact_values_survive_task_context_exit_for_every_family(self):
        for family, assignment in self._actorless_assignments():
            with self.subTest(family=family["name"]):
                assignment.refresh_from_db()
                self.assertEqual(assignment.system_authorization_operation, CHECKOUT_OPERATION)
                self.assertEqual(assignment.system_authorization_reason, REASON)

        component = next(family for family in self.families if family["name"] == "component")
        with TaskContext(tenant_id=self.tenant.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=component["permission"],
                operation=COMPONENT_ALLOCATION_OPERATION,
                reason=REASON,
            )
            allocation = create_component_allocation(
                component["item"],
                1,
                holder=self.holder,
                system_authorization=authorization,
            )
        allocation.refresh_from_db()
        self.assertEqual(allocation.system_authorization_operation, COMPONENT_ALLOCATION_OPERATION)
        self.assertEqual(allocation.system_authorization_reason, REASON)

    def test_component_allocation_service_rejects_overallocation(self):
        component = next(family for family in self.families if family["name"] == "component")
        with TaskContext(tenant_id=self.tenant.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=component["permission"],
                operation=COMPONENT_ALLOCATION_OPERATION,
                reason=REASON,
            )
            with self.assertRaises(ValidationError):
                create_component_allocation(
                    component["item"],
                    21,
                    holder=self.holder,
                    system_authorization=authorization,
                )

        self.assertFalse(ComponentAllocation._base_manager.filter(component=component["item"]).exists())

    def test_trusted_actorless_component_allocation_may_explicitly_overallocate(self):
        component = next(family for family in self.families if family["name"] == "component")
        with TaskContext(tenant_id=self.tenant.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=component["permission"],
                operation=COMPONENT_ALLOCATION_OPERATION,
                reason=REASON,
            )
            allocation = create_component_allocation(
                component["item"],
                21,
                holder=self.holder,
                system_authorization=authorization,
                system_allow_overallocate=True,
            )

        self.assertEqual(allocation.qty, 21)
        self.assertEqual(allocation.system_authorization_reason, REASON)

    def test_human_actor_cannot_use_system_overallocation_override(self):
        component = next(family for family in self.families if family["name"] == "component")
        with self.tenant_context(self.tenant), self.assertRaises(ValidationError):
            create_component_allocation(
                component["item"],
                21,
                holder=self.holder,
                user=self.actor,
                system_allow_overallocate=True,
            )

        self.assertFalse(ComponentAllocation._base_manager.filter(component=component["item"]).exists())

    def _foreign_component_context(self):
        tenant = Tenant.objects.create(name="Foreign Component Tenant", slug="foreign-component-tenant")
        holder = AssetHolder.objects.create(
            first_name="Foreign",
            last_name="Holder",
            upn="foreign-holder@example.invalid",
            tenant=tenant,
        )
        actor = User.objects.create_user(username="foreign-component-actor", password="x")
        role = Role.objects.create(
            tenant=tenant,
            name="Foreign component operator",
            permissions=["inventory.add_componentallocation", "inventory.change_componentallocation"],
        )
        grant(actor, tenant, role)
        source = next(family for family in self.families if family["name"] == "component")["item"]
        component = Component.objects.create(
            name="Foreign Component",
            manufacturer=source.manufacturer,
            category=source.category,
            tenant=tenant,
            allow_overallocate=True,
        )
        return tenant, holder, actor, component

    def test_component_update_denies_foreign_assignment_id(self):
        source = next(family for family in self.families if family["name"] == "component")["item"]
        with self.tenant_context(self.tenant):
            assignment = create_component_allocation(source, 1, holder=self.holder, user=self.actor)
        foreign_tenant, foreign_holder, foreign_actor, foreign_component = self._foreign_component_context()

        with self.tenant_context(foreign_tenant), self.assertRaises(ValidationError):
            update_component_allocation(
                assignment.pk,
                foreign_component,
                1,
                holder=foreign_holder,
                user=foreign_actor,
            )

        assignment.refresh_from_db()
        self.assertEqual(assignment.component_id, source.pk)
        self.assertEqual(assignment.target_tenant_id, self.tenant.pk)

    def test_component_update_cannot_erase_cross_tenant_grant_provenance(self):
        source = next(family for family in self.families if family["name"] == "component")["item"]
        foreign_tenant, foreign_holder, foreign_actor, foreign_component = self._foreign_component_context()
        grant_row = TenantResourceGrant.objects.create(
            tenant=self.tenant,
            grantee_tenant=foreign_tenant,
            resource_type=ContentType.objects.get_for_model(ComponentStock),
            resource_id=ComponentStock._base_manager.get(component=source, location=self.location).pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )
        with self.tenant_context(foreign_tenant):
            assignment = checkout_inventory_item(
                source,
                1,
                holder=foreign_holder,
                source_location=self.location,
                user=foreign_actor,
            )
            with self.assertRaises(ValidationError):
                update_component_allocation(
                    assignment.pk,
                    foreign_component,
                    1,
                    holder=foreign_holder,
                    source_location=None,
                    user=foreign_actor,
                )

        assignment.refresh_from_db()
        self.assertEqual(assignment.component_id, source.pk)
        self.assertEqual(assignment.source_tenant_id, self.tenant.pk)
        self.assertEqual(assignment.target_tenant_id, foreign_tenant.pk)
        self.assertEqual(assignment.resource_grant_id, grant_row.pk)

    def test_human_assignments_have_empty_system_provenance_for_every_family(self):
        with self.tenant_context(self.tenant):
            for family in self.families:
                with self.subTest(family=family["name"]):
                    assignment = checkout_inventory_item(
                        family["item"],
                        1,
                        holder=self.holder,
                        source_location=self.location,
                        user=self.actor,
                    )
                    self.assertIsNone(assignment.system_authorization_operation)
                    self.assertIsNone(assignment.system_authorization_reason)

    def test_instance_rewrite_or_clear_is_denied_for_every_family(self):
        for family, assignment in self._actorless_assignments():
            for operation, reason in (("rewritten", REASON), (CHECKOUT_OPERATION, None)):
                with self.subTest(family=family["name"], operation=operation, reason=reason):
                    assignment.system_authorization_operation = operation
                    assignment.system_authorization_reason = reason
                    with self.assertRaises(ValidationError):
                        with authorized_assignment_write(assignment):
                            assignment.save()
                    assignment.refresh_from_db()

    def test_queryset_rewrite_is_denied_for_every_family(self):
        for family, assignment in self._actorless_assignments():
            with self.subTest(family=family["name"]):
                with self.assertRaises(ValidationError):
                    family["assignment_model"]._base_manager.filter(pk=assignment.pk).update(
                        system_authorization_reason="rewritten"
                    )
                assignment.refresh_from_db()
                self.assertEqual(assignment.system_authorization_reason, REASON)
