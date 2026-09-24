from django.db import IntegrityError, transaction
from django.test import TestCase

from assets.models import Supplier
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, TenantGroup


class SupplierScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Supplier Tenant A", slug="supplier-tenant-a")
        self.group = TenantGroup.objects.create(name="Supplier Group", slug="supplier-group")
        self.tenant.group = self.group
        self.tenant.save(update_fields=["group"])
        self.other_tenant = Tenant.objects.create(name="Supplier Tenant B", slug="supplier-tenant-b")
        self.group_supplier = Supplier.objects.create(
            name="Group Supplier", slug="group-supplier", tenant_group=self.group
        )
        self.tenant_supplier = Supplier.objects.create(name="Tenant Supplier", slug="tenant-supplier", tenant=self.tenant)
        self.other_supplier = Supplier.objects.create(
            name="Other Tenant Supplier", slug="other-tenant-supplier", tenant=self.other_tenant
        )
        self.global_supplier = Supplier.objects.create(name="Global Supplier", slug="global-supplier")

    def test_active_tenant_sees_global_and_group_suppliers_but_not_other_tenants(self):
        with self.tenant_context(self.tenant):
            supplier_ids = set(Supplier.objects.values_list("pk", flat=True))

        self.assertIn(self.global_supplier.pk, supplier_ids)
        self.assertIn(self.group_supplier.pk, supplier_ids)
        self.assertIn(self.tenant_supplier.pk, supplier_ids)
        self.assertNotIn(self.other_supplier.pk, supplier_ids)

    def test_tenant_and_tenant_group_are_mutually_exclusive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Supplier.objects.bulk_create(
                [
                    Supplier(
                        name="Invalid Scope",
                        slug="invalid-scope",
                        tenant=self.tenant,
                        tenant_group=self.group,
                    )
                ]
            )

    def test_name_and_slug_are_unique_within_each_scope_family(self):
        same_scope_rows = [
            ("Tenant Supplier", "tenant-supplier-duplicate", {"tenant": self.tenant}),
            ("Another Tenant Supplier", "tenant-supplier", {"tenant": self.tenant}),
            ("Group Supplier", "group-supplier-duplicate", {"tenant_group": self.group}),
            ("Another Group Supplier", "group-supplier", {"tenant_group": self.group}),
            ("Global Supplier", "global-supplier-duplicate", {}),
            ("Another Global Supplier", "global-supplier", {}),
        ]
        for name, slug, scope in same_scope_rows:
            with self.subTest(name=name, slug=slug), self.assertRaises(IntegrityError), transaction.atomic():
                Supplier.objects.bulk_create([Supplier(name=name, slug=slug, **scope)])

        parallel_scopes = [
            Supplier.objects.create(name="Shared", slug="shared-tenant", tenant=self.other_tenant),
            Supplier.objects.create(name="Shared", slug="shared-group", tenant_group=self.group),
            Supplier.objects.create(name="Shared", slug="shared-global"),
        ]
        self.assertEqual(len(parallel_scopes), 3)

    def test_supplier_selection_forms_accept_global_active_supplier_and_exclude_inactive(self):
        from assets.forms.asset_form import AssetForm
        from assets.forms.request_forms import AssetReceiveForm
        from assets.forms.warranty_form import WarrantyForm
        from inventory.forms.accessory_forms import AccessoryForm
        from inventory.forms.component_forms import ComponentForm
        from inventory.forms.consumable_forms import ConsumableForm
        from licenses.forms import LicenseForm
        from procurement.forms import ContractForm, PurchaseOrderForm

        inactive_supplier = Supplier.objects.create(
            name="Inactive Global Supplier", slug="inactive-global-supplier", is_active=False
        )
        active_supplier = self.global_supplier
        with self.tenant_context(self.tenant):
            form_fields = [
                (AssetForm(), "warranty_supplier"),
                (AssetReceiveForm(), "supplier"),
                (WarrantyForm(), "supplier"),
                (AccessoryForm(), "supplier"),
                (ComponentForm(), "supplier"),
                (ConsumableForm(), "supplier"),
                (LicenseForm(), "supplier"),
                (PurchaseOrderForm(), "supplier"),
                (ContractForm(), "supplier"),
            ]
            for form, field_name in form_fields:
                with self.subTest(form=type(form).__name__, field=field_name):
                    supplier_field = form.fields[field_name]
                    self.assertEqual(supplier_field.clean(str(active_supplier.pk)), active_supplier)
                    self.assertNotIn(inactive_supplier, supplier_field.queryset)
                    self.assertIn(self.group_supplier, supplier_field.queryset)
                    self.assertNotIn(self.other_supplier, supplier_field.queryset)
