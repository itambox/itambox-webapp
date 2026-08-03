"""Direct assignment writes must be denied before stock moves (#194).

``AbstractAssignment.clean()`` refuses an unpermitted create, but the concrete
``save()`` adjusts the source pool *first* and validates second. These tests
pin the guard at the only point that closes that window --
``inventory.stock.adjust_inventory_stock`` -- so a denied create leaves no row
AND no stock movement, for every assignment family, same-tenant and
cross-tenant, with and without a valid ``use`` grant.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.db.models.signals import pre_delete
from django.test import TestCase
from django.urls import reverse

from assets.models import Category, Manufacturer
from core.tests.mixins import TenantTestMixin
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
from inventory.models_assignment_write import (
    authorized_assignment_validation,
    authorized_assignment_write,
)
from inventory.services import purge_inventory_assignment
from organization.models import AssetHolder, Location, Site, Tenant, TenantResourceGrant

STOCK_QTY = 10
CHECKOUT_QTY = 2
User = get_user_model()


class DirectAssignmentWriteDenialTests(TenantTestMixin, TestCase):
    """Every family, both write spellings, both tenancies."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = Tenant.objects.create(name="DAW Owner", slug="daw-owner")
        cls.grantee = Tenant.objects.create(name="DAW Grantee", slug="daw-grantee")

        owner_site = Site.objects.create(name="DAW Owner Site", slug="daw-owner-site", tenant=cls.owner)
        grantee_site = Site.objects.create(name="DAW Grantee Site", slug="daw-grantee-site", tenant=cls.grantee)
        cls.owner_location = Location.objects.create(
            name="DAW Owner Depot",
            slug="daw-owner-depot",
            site=owner_site,
            tenant=cls.owner,
        )
        cls.owner_holder = AssetHolder.objects.create(
            first_name="DAW",
            last_name="Owner",
            upn="daw-owner@example.invalid",
            tenant=cls.owner,
        )
        cls.grantee_holder = AssetHolder.objects.create(
            first_name="DAW",
            last_name="Grantee",
            upn="daw-grantee@example.invalid",
            tenant=cls.grantee,
        )
        cls.grantee_location = Location.objects.create(
            name="DAW Grantee Depot",
            slug="daw-grantee-depot",
            site=grantee_site,
            tenant=cls.grantee,
        )

        manufacturer = Manufacturer.objects.create(name="DAW Manufacturer", slug="daw-manufacturer")
        definitions = (
            ("accessory", Accessory, AccessoryStock, AccessoryAssignment),
            ("component", Component, ComponentStock, ComponentAllocation),
            ("consumable", Consumable, ConsumableStock, ConsumableAssignment),
        )
        cls.families = []
        for name, item_model, stock_model, assignment_model in definitions:
            category = Category.objects.create(
                name=f"DAW {name.title()}",
                slug=f"daw-{name}",
                applies_to={name: True},
            )
            item = item_model.objects.create(
                name=f"DAW {name.title()}",
                manufacturer=manufacturer,
                category=category,
                tenant=cls.owner,
            )
            stock = stock_model.objects.create(**{name: item}, location=cls.owner_location, qty=STOCK_QTY)
            cls.families.append(
                {
                    "name": name,
                    "item": item,
                    "item_field": name,
                    "stock": stock,
                    "stock_model": stock_model,
                    "assignment_model": assignment_model,
                }
            )

    # ------------------------------------------------------------------ utils

    def _kwargs(self, family, holder):
        return {
            family["item_field"]: family["item"],
            "assigned_holder": holder,
            "qty": CHECKOUT_QTY,
            "from_location": self.owner_location,
        }

    def _use_grant(self, family):
        return TenantResourceGrant.objects.create(
            tenant=self.owner,
            grantee_tenant=self.grantee,
            resource_type=ContentType.objects.get_for_model(family["stock_model"]),
            resource_id=family["stock"].pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )

    def _assert_nothing_happened(self, family):
        """No assignment row, and the source pool is untouched."""
        self.assertEqual(family["assignment_model"]._base_manager.count(), 0)
        family["stock"].refresh_from_db()
        self.assertEqual(family["stock"].qty, STOCK_QTY)

    def _create_permitted(self, family):
        assignment = family["assignment_model"](**self._kwargs(family, self.owner_holder))
        with authorized_assignment_write(assignment):
            assignment.save()
        return assignment

    # ------------------------------------------------------------ same tenant

    def test_manager_create_is_denied_before_stock_moves(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                with self.assertRaises(ValidationError):
                    family["assignment_model"].objects.create(**self._kwargs(family, self.owner_holder))
                self._assert_nothing_happened(family)

    def test_instance_save_is_denied_before_stock_moves(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = family["assignment_model"](**self._kwargs(family, self.owner_holder))
                with self.assertRaises(ValidationError):
                    assignment.save()
                self._assert_nothing_happened(family)

    def test_base_manager_create_is_denied_before_stock_moves(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                with self.assertRaises(ValidationError):
                    family["assignment_model"]._base_manager.create(**self._kwargs(family, self.owner_holder))
                self._assert_nothing_happened(family)

    def test_bulk_create_is_denied_before_stock_moves(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = family["assignment_model"](**self._kwargs(family, self.owner_holder))
                with self.assertRaises(ValidationError):
                    family["assignment_model"]._base_manager.bulk_create([assignment])
                self._assert_nothing_happened(family)

    def test_manager_upsert_factories_are_denied_before_stock_moves(self):
        for method_name in ("get_or_create", "update_or_create"):
            for family in self.families:
                with self.subTest(method=method_name, family=family["name"]):
                    with self.assertRaises(ValidationError):
                        getattr(family["assignment_model"]._base_manager, method_name)(
                            **self._kwargs(family, self.owner_holder)
                        )
                    self._assert_nothing_happened(family)

    def test_queryset_update_and_bulk_update_cannot_mutate_existing_assignment(self):
        for method_name in ("update", "bulk_update"):
            for family in self.families:
                with self.subTest(method=method_name, family=family["name"]):
                    family["stock"].refresh_from_db()
                    stock_before = family["stock"].qty
                    assignment = self._create_permitted(family)
                    assignment.qty = CHECKOUT_QTY + 5
                    with self.assertRaises(ValidationError):
                        if method_name == "update":
                            family["assignment_model"]._base_manager.filter(pk=assignment.pk).update(qty=assignment.qty)
                        else:
                            family["assignment_model"]._base_manager.bulk_update([assignment], ["qty"])
                    assignment.refresh_from_db()
                    self.assertEqual(assignment.qty, CHECKOUT_QTY)
                    family["stock"].refresh_from_db()
                    self.assertEqual(family["stock"].qty, stock_before - CHECKOUT_QTY)

    def test_queryset_delete_cannot_bypass_stock_restoration(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = self._create_permitted(family)
                with self.assertRaises(ValidationError):
                    family["assignment_model"]._base_manager.filter(pk=assignment.pk).delete()
                self.assertTrue(family["assignment_model"]._base_manager.filter(pk=assignment.pk).exists())
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY - CHECKOUT_QTY)

    def test_queryset_raw_delete_cannot_bypass_stock_restoration(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = self._create_permitted(family)
                with self.assertRaises(ValidationError):
                    family["assignment_model"]._base_manager.filter(pk=assignment.pk)._raw_delete(using="default")
                self.assertTrue(family["assignment_model"]._base_manager.filter(pk=assignment.pk).exists())

    def test_hard_delete_signal_cannot_reuse_collector_permit_for_arbitrary_update(self):
        family = next(candidate for candidate in self.families if candidate["name"] == "component")
        assignment = self._create_permitted(family)
        category = Category.objects.create(
            name="DAW Disposable Category",
            slug="daw-disposable-category",
            applies_to={"component": True},
        )
        denied = []

        def attempt_assignment_rewrite(**kwargs):
            try:
                ComponentAllocation._base_manager.filter(pk=assignment.pk).update(qty=999)
            except ValidationError:
                denied.append(True)

        pre_delete.connect(attempt_assignment_rewrite, sender=Category, weak=False)
        try:
            category.delete(force_hard_delete=True)
        finally:
            pre_delete.disconnect(attempt_assignment_rewrite, sender=Category)

        assignment.refresh_from_db()
        self.assertEqual(denied, [True])
        self.assertEqual(assignment.qty, CHECKOUT_QTY)

    def test_hard_delete_signal_cannot_reuse_matching_collector_update(self):
        family = next(candidate for candidate in self.families if candidate["name"] == "component")
        location = Location.objects.create(
            name="DAW Disposable Source",
            slug="daw-disposable-source",
            site=self.owner_location.site,
            tenant=self.owner,
        )
        stock = ComponentStock.objects.create(component=family["item"], location=location, qty=STOCK_QTY)
        assignment = ComponentAllocation(
            component=family["item"],
            assigned_holder=self.owner_holder,
            from_location=location,
            qty=CHECKOUT_QTY,
        )
        with authorized_assignment_write(assignment):
            assignment.save()
        stock.delete(force_hard_delete=True)
        denied = []

        def attempt_matching_update(**kwargs):
            try:
                ComponentAllocation._base_manager.filter(pk=assignment.pk).update(from_location=None)
            except ValidationError:
                denied.append(True)

        pre_delete.connect(attempt_matching_update, sender=Location, weak=False)
        try:
            location.delete(force_hard_delete=True)
        finally:
            pre_delete.disconnect(attempt_matching_update, sender=Location)

        assignment.refresh_from_db()
        self.assertEqual(denied, [True])
        self.assertIsNone(assignment.from_location_id)

    def test_existing_instance_save_is_denied_before_stock_moves(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = self._create_permitted(family)
                assignment.qty = CHECKOUT_QTY + 3

                with self.assertRaises(ValidationError):
                    assignment.save()

                assignment.refresh_from_db()
                self.assertEqual(assignment.qty, CHECKOUT_QTY)
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY - CHECKOUT_QTY)

    def test_existing_instance_delete_is_denied_before_stock_moves(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = self._create_permitted(family)

                with self.assertRaises(ValidationError):
                    assignment.delete()

                self.assertTrue(family["assignment_model"]._base_manager.filter(pk=assignment.pk).exists())
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY - CHECKOUT_QTY)

    def test_hard_purge_service_removes_soft_assignment_without_restoring_stock_twice(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = self._create_permitted(family)
                with authorized_assignment_write(assignment):
                    assignment.delete()
                assignment = family["assignment_model"]._base_manager.get(pk=assignment.pk)
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY)

                with self.assertRaises(ValidationError):
                    assignment.delete(force_hard_delete=True)
                assignment_pk = assignment.pk
                purge_inventory_assignment(assignment)

                self.assertFalse(family["assignment_model"]._base_manager.filter(pk=assignment_pk).exists())
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY)

    def test_assignment_pre_delete_signal_cannot_reuse_hard_purge_for_raw_delete(self):
        family = next(candidate for candidate in self.families if candidate["name"] == "component")
        assignment = self._create_permitted(family)
        with authorized_assignment_write(assignment):
            assignment.delete()
        assignment = ComponentAllocation._base_manager.get(pk=assignment.pk)
        assignment_pk = assignment.pk
        denied = []

        def attempt_matching_raw_delete(instance, **kwargs):
            try:
                ComponentAllocation._base_manager.filter(pk=instance.pk)._raw_delete(using="default")
            except ValidationError:
                denied.append(True)

        pre_delete.connect(attempt_matching_raw_delete, sender=ComponentAllocation, weak=False)
        try:
            purge_inventory_assignment(assignment)
        finally:
            pre_delete.disconnect(attempt_matching_raw_delete, sender=ComponentAllocation)

        self.assertEqual(denied, [True])
        self.assertFalse(ComponentAllocation._base_manager.filter(pk=assignment_pk).exists())

    def test_purge_deleted_command_physically_removes_all_assignment_families_once(self):
        assignment_pks = []
        for family in self.families:
            assignment = self._create_permitted(family)
            with authorized_assignment_write(assignment):
                assignment.delete()
            assignment_pks.append((family, assignment.pk))

        call_command("purge_deleted", days=-1, verbosity=0)

        for family, assignment_pk in assignment_pks:
            with self.subTest(family=family["name"]):
                self.assertFalse(family["assignment_model"]._base_manager.filter(pk=assignment_pk).exists())
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY)

    def test_bulk_purge_view_removes_all_assignment_families_without_stock_change(self):
        user = User.objects.create_superuser(username="assignment-bulk-purge", password="x")
        self.client.force_login(user)
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = self._create_permitted(family)
                with authorized_assignment_write(assignment):
                    assignment.delete()
                assignment_pk = assignment.pk
                content_type = ContentType.objects.get_for_model(family["assignment_model"])
                response = self.client.post(
                    reverse("object_bulk_purge", kwargs={"content_type_id": content_type.pk}),
                    {"pk": [assignment_pk]},
                )
                self.assertEqual(response.status_code, 302)
                self.assertFalse(family["assignment_model"]._base_manager.filter(pk=assignment_pk).exists())
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY)

    def test_authorized_failed_update_rolls_back_stock_and_assignment(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = self._create_permitted(family)
                assignment.assigned_location = self.grantee_location
                assignment.qty = CHECKOUT_QTY + 1

                with self.assertRaises((ValidationError, IntegrityError)):
                    with authorized_assignment_write(assignment):
                        assignment.save()

                assignment.refresh_from_db()
                self.assertEqual(assignment.qty, CHECKOUT_QTY)
                self.assertIsNone(assignment.assigned_location_id)
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY - CHECKOUT_QTY)

    # ----------------------------------------------------------- cross tenant

    def test_cross_tenant_create_without_grant_is_denied_before_stock_moves(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                with self.assertRaises(ValidationError):
                    family["assignment_model"].objects.create(**self._kwargs(family, self.grantee_holder))
                self._assert_nothing_happened(family)

    def test_cross_tenant_create_with_valid_use_grant_is_still_denied(self):
        """A grant authorizes the tenancy, never the bypass."""
        for family in self.families:
            with self.subTest(family=family["name"]):
                grant = self._use_grant(family)
                kwargs = self._kwargs(family, self.grantee_holder)
                with self.assertRaises(ValidationError):
                    family["assignment_model"].objects.create(resource_grant=grant, **kwargs)
                self._assert_nothing_happened(family)

    # ------------------------------------------------------------ capabilities

    def test_validation_only_permit_cannot_authorize_a_save(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = family["assignment_model"](**self._kwargs(family, self.owner_holder))
                with authorized_assignment_validation():
                    # The permit exists so a form can exercise model invariants...
                    assignment.clean()
                    # ...and stops dead at the write.
                    with self.assertRaises(ValidationError):
                        assignment.save()
                self._assert_nothing_happened(family)

    def test_permit_is_bound_to_the_exact_instance_and_fingerprint(self):
        for family in self.families:
            with self.subTest(family=family["name"]):
                permitted = family["assignment_model"](**self._kwargs(family, self.owner_holder))
                other = family["assignment_model"](**self._kwargs(family, self.owner_holder))
                with authorized_assignment_write(permitted):
                    # A different instance of the same shape is not the permitted write.
                    with self.assertRaises(ValidationError):
                        other.save()
                    # Mutating the permitted instance invalidates its own fingerprint.
                    permitted.qty = CHECKOUT_QTY + 1
                    with self.assertRaises(ValidationError):
                        permitted.save()
                self._assert_nothing_happened(family)

    def test_permitted_write_creates_the_row_and_moves_stock(self):
        """Positive control: the guard denies bypasses, not sanctioned writes."""
        for family in self.families:
            with self.subTest(family=family["name"]):
                assignment = family["assignment_model"](**self._kwargs(family, self.owner_holder))
                with authorized_assignment_write(assignment):
                    assignment.save()
                self.assertIsNotNone(assignment.pk)
                family["stock"].refresh_from_db()
                self.assertEqual(family["stock"].qty, STOCK_QTY - CHECKOUT_QTY)
