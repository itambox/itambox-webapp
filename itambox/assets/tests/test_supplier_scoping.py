import uuid

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from assets.models import Supplier
from core.managers import set_current_tenant, set_current_tenant_group
from core.models import ObjectChange
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin
from itambox.middleware import _current_user, _request_id
from organization.models import Tenant, TenantGroup
from subscriptions.models import Subscription


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
        self.tenant_supplier = Supplier.objects.create(
            name="Tenant Supplier", slug="tenant-supplier", tenant=self.tenant
        )
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

    def test_group_scoped_supplier_changes_are_attributed_to_the_active_tenant(self):
        # A group-scoped row (tenant=None) is shared with a bounded audience, not
        # system-wide: its audit snapshots must never become globally visible
        # through ObjectChange.allow_global_tenant.
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk):
            self.group_supplier.notes = "Group notes updated"
            self.group_supplier.save()

        with self.tenant_context(self.tenant):
            change = ObjectChange.objects.get(
                changed_object_type=ContentType.objects.get_for_model(Supplier),
                changed_object_id=self.group_supplier.pk,
            )
        self.assertEqual(change.tenant_id, self.tenant.pk)

    def test_group_workspace_changes_fan_out_to_the_group_audience(self):
        # In a tenant-group workspace no single tenant is active: the group-scoped
        # row must still never produce a globally visible (tenant=None) snapshot.
        # It fans out to the live tenants of the group's subtree — descendant
        # groups included — and leaks to nobody else.
        sibling = Tenant.objects.create(
            name="Supplier Tenant Sibling", slug="supplier-tenant-sibling", group=self.group
        )
        child_group = TenantGroup.objects.create(
            name="Supplier Child Group", slug="supplier-child-group", parent=self.group
        )
        child_tenant = Tenant.objects.create(
            name="Supplier Tenant Child", slug="supplier-tenant-child", group=child_group
        )

        _current_user.set(self.tenant_user)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        _request_id.set(uuid.uuid4())
        try:
            self.group_supplier.notes = "Group workspace update"
            self.group_supplier.save()
        finally:
            _request_id.set(None)
            _current_user.set(None)
            set_current_tenant_group(None)
            set_current_tenant(None)

        changes = ObjectChange._base_manager.filter(
            changed_object_type=ContentType.objects.get_for_model(Supplier),
            changed_object_id=self.group_supplier.pk,
        )
        self.assertFalse(changes.filter(tenant__isnull=True).exists())
        self.assertEqual(
            set(changes.values_list("tenant_id", flat=True)),
            {self.tenant.pk, sibling.pk, child_tenant.pk},
        )

        # A tenant inside the group still sees exactly its own row through the
        # normal scoped manager; an unrelated tenant sees nothing at all.
        set_current_tenant(sibling)
        _current_user.set(self.tenant_user)
        try:
            visible = set(
                ObjectChange.objects.filter(
                    changed_object_type=ContentType.objects.get_for_model(Supplier),
                    changed_object_id=self.group_supplier.pk,
                ).values_list("tenant_id", flat=True)
            )
        finally:
            set_current_tenant(None)
            _current_user.set(None)
        self.assertEqual(visible, {sibling.pk})

        set_current_tenant(self.other_tenant)
        _current_user.set(self.tenant_user)
        try:
            leaked = ObjectChange.objects.filter(
                changed_object_type=ContentType.objects.get_for_model(Supplier),
                changed_object_id=self.group_supplier.pk,
            ).exists()
        finally:
            set_current_tenant(None)
            _current_user.set(None)
        self.assertFalse(leaked)

    def test_global_supplier_changes_stay_system_wide(self):
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk):
            self.global_supplier.notes = "Global notes updated"
            self.global_supplier.save()

        with self.tenant_context(self.tenant):
            change = ObjectChange.objects.get(
                changed_object_type=ContentType.objects.get_for_model(Supplier),
                changed_object_id=self.global_supplier.pk,
            )
        self.assertIsNone(change.tenant_id)


class SupplierListSubscriptionCountTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(
            name="Count Tenant",
            slug="count-tenant",
            permissions=["assets.view_supplier"],
        )
        self.group = TenantGroup.objects.create(name="Count Group", slug="count-group")
        self.tenant.group = self.group
        self.tenant.save(update_fields=["group"])
        self.supplier = Supplier.objects.create(name="Counted Vendor", slug="counted-vendor")
        self.other_tenant = Tenant.objects.create(name="Count Other", slug="count-other")
        Subscription.objects.create(name="Live subscription", supplier=self.supplier, tenant=self.tenant)
        soft_deleted = Subscription.objects.create(
            name="Soft-deleted subscription", supplier=self.supplier, tenant=self.tenant
        )
        soft_deleted.soft_delete()
        # Global suppliers can be referenced from any tenant; the count must still
        # show only the active tenant's live subscriptions.
        Subscription.objects.create(name="Foreign subscription", supplier=self.supplier, tenant=self.other_tenant)

    def test_list_count_matches_the_scoped_detail_tab(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.get(reverse("assets:supplier_list"))

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context["table"].data if row.pk == self.supplier.pk)
        self.assertEqual(row.subscription_count, 1)

    def test_group_workspace_count_matches_the_scoped_detail_tab(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session["active_tenant_group_id"] = self.group.pk
        session.save()

        response = self.client.get(reverse("assets:supplier_list"))

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context["table"].data if row.pk == self.supplier.pk)
        # The unrelated tenant sits outside the group; only the member tenant's
        # live subscription counts — exactly what the scoped detail tab exposes.
        self.assertEqual(row.subscription_count, 1)
