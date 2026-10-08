"""Explicit scoping declarations of the inventory forms (#584, WP3).

The inventory forms declare tenant scoping, tenant requiredness and TomSelect
behaviour through ``TenantScopedFormMixin`` instead of relying on the global
form patches. These assertions pin each declaration directly so they stay true
when the patches are removed.
"""

from django.test import TestCase
from model_bakery import baker

from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from inventory.forms import (
    AccessoryForm,
    AccessoryStockForm,
    AccessoryStockModalForm,
    ComponentAllocationForm,
    ComponentForm,
    ComponentStockForm,
    ComponentStockModalForm,
    ConsumableForm,
    ConsumableStockForm,
    ConsumableStockModalForm,
    KitForm,
    KitItemForm,
)
from inventory.models import Accessory, Component, ComponentAllocation, Consumable, Kit
from itambox.middleware import set_current_user
from organization.models import Location, Site, Tenant

FORMS = (
    AccessoryForm,
    AccessoryStockForm,
    AccessoryStockModalForm,
    ComponentAllocationForm,
    ComponentForm,
    ComponentStockForm,
    ComponentStockModalForm,
    ConsumableForm,
    ConsumableStockForm,
    ConsumableStockModalForm,
    KitForm,
    KitItemForm,
)

TENANT_REQUIRED_FORMS = (AccessoryForm, ComponentForm, ConsumableForm, KitForm)


class InventoryFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="ifs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="ifs-b")
        site = baker.make(Site, tenant=self.tenant)
        self.loc_a = baker.make(Location, tenant=self.tenant, site=site)
        self.loc_b = baker.make(Location, tenant=self.tenant_b, site=site)
        self.component_a = baker.make(Component, tenant=self.tenant)
        self.component_b = baker.make(Component, tenant=self.tenant_b)
        self.accessory_a = baker.make(Accessory, tenant=self.tenant)
        self.accessory_b = baker.make(Accessory, tenant=self.tenant_b)
        self.consumable_a = baker.make(Consumable, tenant=self.tenant)
        self.consumable_b = baker.make(Consumable, tenant=self.tenant_b)
        self.kit_a = baker.make(Kit, tenant=self.tenant)
        self.kit_b = baker.make(Kit, tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def tearDown(self):
        set_current_user(None)
        set_current_tenant(None)
        self.clear_tenant_context()

    @staticmethod
    def _pks(form, name):
        return set(form.fields[name].queryset.values_list("pk", flat=True))

    def test_forms_use_the_explicit_mixin(self):
        for form_class in FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertTrue(issubclass(form_class, TenantScopedFormMixin))

    def test_tenant_requiredness_declarations(self):
        for form_class in TENANT_REQUIRED_FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertIs(form_class.tenant_required, True)
                self.assertIs(form_class.tenant_autoset_when_single, True)
                self.assertIs(form_class().fields["tenant"].required, True)

    def test_model_choice_fields_are_scoped_explicitly(self):
        expected = (
            (AccessoryForm, ("tenant",)),
            (ComponentForm, ("tenant",)),
            (ConsumableForm, ("tenant",)),
            (KitForm, ("tenant",)),
            (AccessoryStockForm, ("accessory", "location")),
            (ComponentStockForm, ("component", "location")),
            (ConsumableStockForm, ("consumable", "location")),
            (AccessoryStockModalForm, ("location",)),
            (ComponentStockModalForm, ("location",)),
            (ConsumableStockModalForm, ("location",)),
            (KitItemForm, ("kit", "accessory", "consumable")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_allocation_source_location_stays_non_scoped(self):
        self.assertEqual(ComponentAllocationForm.tenant_scoped_choice_exclusions, ("from_location",))
        create_form = ComponentAllocationForm()
        self.assertNotIn("from_location", create_form.fields)
        for name in ("component", "assigned_holder", "assigned_location"):
            with self.subTest(field=name):
                self.assertTrue(is_tenant_scoped_field(create_form.fields[name]))
        # Update path: the recorded source is rebuilt from the base manager and
        # must never be converted to read-time tenant scoping.
        update_form = ComponentAllocationForm(instance=ComponentAllocation(pk=1, component=self.component_a))
        self.assertFalse(is_tenant_scoped_field(update_form.fields["from_location"]))

    def test_stock_forms_follow_the_active_tenant(self):
        for form_class, item_field, own, foreign in (
            (AccessoryStockForm, "accessory", self.accessory_a, self.accessory_b),
            (ComponentStockForm, "component", self.component_a, self.component_b),
            (ConsumableStockForm, "consumable", self.consumable_a, self.consumable_b),
        ):
            form = form_class()
            with self.subTest(form=form_class.__name__):
                self.assertEqual(self._pks(form, item_field), {own.pk})
                self.assertNotIn(foreign.pk, self._pks(form, item_field))
                self.assertEqual(self._pks(form, "location"), {self.loc_a.pk})

    def test_modal_and_kit_item_forms_follow_the_active_tenant(self):
        for form_class in (AccessoryStockModalForm, ComponentStockModalForm, ConsumableStockModalForm):
            with self.subTest(form=form_class.__name__):
                self.assertEqual(self._pks(form_class(), "location"), {self.loc_a.pk})
        form = KitItemForm()
        self.assertEqual(self._pks(form, "kit"), {self.kit_a.pk})
        self.assertEqual(self._pks(form, "accessory"), {self.accessory_a.pk})
        self.assertEqual(self._pks(form, "consumable"), {self.consumable_a.pk})

    def test_bound_foreign_object_is_rejected(self):
        form = ComponentStockForm(
            data={"component": self.component_b.pk, "location": self.loc_a.pk, "qty": 1},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("component", form.errors)
        form = ComponentStockForm(
            data={"component": self.component_a.pk, "location": self.loc_b.pk, "qty": 1},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("location", form.errors)

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = (
            (AccessoryForm, ("manufacturer", "category", "tenant")),
            (ComponentForm, ("manufacturer", "category", "tenant")),
            (ConsumableForm, ("manufacturer", "category", "tenant")),
            (KitForm, ("tenant",)),
            (AccessoryStockForm, ("accessory", "location")),
            (ComponentStockForm, ("component", "location")),
            (ConsumableStockForm, ("consumable", "location")),
            (ComponentAllocationForm, ("component", "assigned_holder", "assigned_location", "assigned_asset")),
            (KitItemForm, ("kit", "asset_type", "accessory", "consumable")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)
