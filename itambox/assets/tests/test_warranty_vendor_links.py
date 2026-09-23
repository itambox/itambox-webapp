"""Issue #500: the Warranty vendor is the shared Supplier link, on every asset surface.

The free-text ``Warranty.provider`` column is gone for good (no legacy field, no
conversion, no dual display): a warranty points at the ``assets.Supplier``
catalogue instead. This module pins the model and form contract, the inline
AssetForm warranty, the rendered detail/list/table/filter/admin surfaces, and
the four-scope matrix from the implementation plan (single tenant, tenant group,
all-accessible, superuser/global).

Scope rule under test: Suppliers are global reference data, so every authorized
scope sees the same active suppliers, while asset reach stays tenant-scoped - a
warranty can never be attached to an asset the scope cannot reach.
"""

import datetime

import django_tables2 as tables
from django import forms
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import models
from django.test import RequestFactory, TestCase
from django.urls import reverse
from model_bakery import baker

from assets.admin import WarrantyAdmin
from assets.filters import WarrantyFilterSet
from assets.forms.asset_form import AssetForm
from assets.forms.warranty_form import WarrantyForm
from assets.models import Asset, AssetRole, AssetType, Manufacturer, StatusLabel, Supplier, Warranty
from assets.models.choices import WarrantyTypeChoices
from assets.tables import WarrantyTable
from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import TenantTestMixin, grant
from itambox.middleware import _current_user
from organization.models import Membership, Role, Tenant, TenantGroup

User = get_user_model()

TODAY = datetime.date.today()
ONE_YEAR = datetime.timedelta(days=365)
ASSET_PERMS = ["assets.view_asset", "assets.add_asset", "assets.change_asset"]
WARRANTY_PERMS = ["assets.view_warranty", "assets.add_warranty", "assets.change_warranty"]


def warranty_payload(asset, **overrides):
    """Minimal valid WarrantyForm payload; the supplier link stays optional."""
    data = {
        "asset": asset.pk,
        "warranty_type": WarrantyTypeChoices.HARDWARE.value,
        "start_date": TODAY.isoformat(),
        "end_date": (TODAY + ONE_YEAR).isoformat(),
    }
    data.update(overrides)
    return data


def create_warranty(asset, supplier=None, **overrides):
    fields = {
        "asset": asset,
        "warranty_type": WarrantyTypeChoices.HARDWARE,
        "supplier": supplier,
        "start_date": TODAY,
        "end_date": TODAY + ONE_YEAR,
    }
    fields.update(overrides)
    return Warranty.objects.create(**fields)


class WarrantySupplierModelTests(TestCase):
    def setUp(self):
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset = baker.make(Asset, name="Model Laptop", status=self.status, tenant=None)
        self.supplier = Supplier.objects.create(name="Dell Direct", slug="dell-direct")

    def test_free_text_provider_column_is_replaced_by_the_supplier_link(self):
        field_names = {field.name for field in Warranty._meta.get_fields()}
        self.assertNotIn("provider", field_names)

        field = Warranty._meta.get_field("supplier")
        self.assertIs(field.remote_field.model, Supplier)
        self.assertIs(field.remote_field.on_delete, models.SET_NULL)
        self.assertEqual(field.remote_field.related_name, "warranties")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_warranty_is_valid_without_a_supplier(self):
        warranty = create_warranty(self.asset)
        self.assertIsNone(warranty.supplier_id)

    def test_supplier_reverse_relation_lists_its_warranties(self):
        warranty = create_warranty(self.asset, supplier=self.supplier)
        self.assertEqual(list(self.supplier.warranties.all()), [warranty])

    def test_purging_a_supplier_keeps_the_warranty_and_clears_the_link(self):
        warranty = create_warranty(self.asset, supplier=self.supplier)

        self.supplier.delete(force_hard_delete=True)

        warranty.refresh_from_db()
        self.assertIsNone(warranty.supplier_id)
        self.assertTrue(Warranty._base_manager.filter(pk=warranty.pk).exists())


class WarrantyFormSupplierTests(TestCase):
    def setUp(self):
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset = baker.make(Asset, name="Form Laptop", status=self.status, tenant=None)
        self.supplier = Supplier.objects.create(name="CDW Deutschland", slug="cdw-deutschland")
        self.retired_supplier = Supplier.objects.create(name="Retired Vendor", slug="retired-vendor")
        self.retired_supplier.soft_delete()

    def test_form_replaces_the_provider_text_field_with_an_optional_select(self):
        form = WarrantyForm()
        self.assertNotIn("provider", form.fields)

        field = form.fields["supplier"]
        self.assertFalse(field.required)
        self.assertIsInstance(field.widget, forms.Select)

    def test_supplier_choices_are_active_suppliers_only(self):
        choices = set(WarrantyForm().fields["supplier"].queryset.values_list("pk", flat=True))
        self.assertIn(self.supplier.pk, choices)
        self.assertNotIn(self.retired_supplier.pk, choices)

    def test_form_persists_the_supplier_link(self):
        form = WarrantyForm(data=warranty_payload(self.asset, supplier=self.supplier.pk))
        self.assertTrue(form.is_valid(), form.errors)

        warranty = form.save()
        warranty.refresh_from_db()
        self.assertEqual(warranty.supplier_id, self.supplier.pk)

    def test_form_accepts_a_blank_supplier(self):
        form = WarrantyForm(data=warranty_payload(self.asset))
        self.assertTrue(form.is_valid(), form.errors)

        warranty = form.save()
        self.assertIsNone(warranty.supplier_id)


class AssetFormInlineWarrantyTests(TestCase):
    def setUp(self):
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset_role = baker.make(AssetRole, name="Inline Workstation")
        self.asset_type = baker.make(AssetType, model="Latitude 5550")
        self.asset = baker.make(
            Asset, name="Inline Laptop", status=self.status, tenant=None, asset_type=self.asset_type
        )
        self.supplier = Supplier.objects.create(name="Bechtle AG", slug="bechtle-ag")

    def base_data(self, **overrides):
        data = {
            "name": "Inline Laptop",
            "asset_tag": "INLINE-1",
            "asset_type": self.asset_type.pk,
            "asset_role": self.asset_role.pk,
            "status": self.status.pk,
        }
        data.update(overrides)
        return data

    def test_asset_form_replaces_the_warranty_provider_text_input(self):
        form = AssetForm()
        self.assertNotIn("warranty_provider", form.fields)

        field = form.fields["warranty_supplier"]
        self.assertFalse(field.required)
        self.assertIsInstance(field.widget, forms.Select)
        self.assertIn(self.supplier.pk, set(field.queryset.values_list("pk", flat=True)))

    def test_warranty_supplier_is_part_of_the_activation_tuple(self):
        form = AssetForm(data=self.base_data(warranty_supplier=self.supplier.pk))

        self.assertFalse(form.is_valid())
        self.assertIn("warranty_start_date", form.errors)
        self.assertIn("warranty_end_date", form.errors)

    def test_no_warranty_input_creates_nothing(self):
        form = AssetForm(data=self.base_data())
        self.assertTrue(form.is_valid(), form.errors)

        self.assertIsNone(form.create_inline_warranty(self.asset))
        self.assertFalse(Warranty.objects.filter(asset=self.asset).exists())

    def test_inline_warranty_persists_the_supplier_link(self):
        form = AssetForm(
            data=self.base_data(
                warranty_supplier=self.supplier.pk,
                warranty_type=WarrantyTypeChoices.EXTENDED.value,
                warranty_start_date=TODAY.isoformat(),
                warranty_end_date=(TODAY + ONE_YEAR).isoformat(),
                warranty_cost="199.00",
            )
        )
        self.assertTrue(form.is_valid(), form.errors)

        warranty = form.create_inline_warranty(self.asset)
        self.assertIsNotNone(warranty)
        self.assertEqual(warranty.asset_id, self.asset.pk)
        self.assertEqual(warranty.supplier_id, self.supplier.pk)
        self.assertEqual(warranty.warranty_type, WarrantyTypeChoices.EXTENDED)

    def test_inline_warranty_without_a_supplier_keeps_the_dates_only(self):
        form = AssetForm(
            data=self.base_data(
                warranty_start_date=TODAY.isoformat(),
                warranty_end_date=(TODAY + ONE_YEAR).isoformat(),
            )
        )
        self.assertTrue(form.is_valid(), form.errors)

        warranty = form.create_inline_warranty(self.asset)
        self.assertIsNotNone(warranty)
        self.assertIsNone(warranty.supplier_id)


class WarrantySupplierSurfaceTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=ASSET_PERMS + WARRANTY_PERMS)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset = baker.make(Asset, name="Surface Laptop", status=self.status, tenant=self.tenant)
        self.supplier = Supplier.objects.create(name="Also Deutschland", slug="also-deutschland")
        # BaseFilterSet rescopes Supplier choices to the tenant's relations, so the
        # asset link keeps the supplier selectable in the scoped filter set.
        self.asset.supplier = self.supplier
        self.asset.save(update_fields=["supplier"])
        self.retired_supplier = Supplier.objects.create(name="Retired Vendor", slug="retired-vendor")
        self.retired_supplier.soft_delete()
        self.warranty = create_warranty(self.asset, supplier=self.supplier)
        self.warranty_without_supplier = create_warranty(self.asset)
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def test_detail_page_links_the_supplier(self):
        response = self.client.get(reverse("assets:warranty_detail", kwargs={"pk": self.warranty.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supplier:")
        self.assertContains(response, self.supplier.name)
        self.assertContains(response, reverse("assets:supplier_detail", kwargs={"pk": self.supplier.pk}))

    def test_detail_page_without_a_supplier_shows_the_placeholder(self):
        response = self.client.get(reverse("assets:warranty_detail", kwargs={"pk": self.warranty_without_supplier.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not specified")
        self.assertNotContains(response, reverse("assets:supplier_detail", kwargs={"pk": self.supplier.pk}))

    def test_list_page_shows_the_supplier_column_and_link(self):
        response = self.client.get(reverse("assets:warranty_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supplier")
        self.assertContains(response, self.supplier.name)
        self.assertContains(response, reverse("assets:supplier_detail", kwargs={"pk": self.supplier.pk}))

    def test_table_column_links_the_supplier(self):
        column = WarrantyTable.base_columns["supplier"]
        self.assertIsInstance(column, tables.LinkColumn)
        self.assertEqual(str(column.accessor), "supplier.name")
        self.assertEqual(str(column.verbose_name), "Supplier")

        table = WarrantyTable(Warranty.objects.filter(pk=self.warranty.pk))
        cell = table.rows[0].get_cell("supplier")
        self.assertIn(self.supplier.name, cell)
        self.assertIn(reverse("assets:supplier_detail", kwargs={"pk": self.supplier.pk}), cell)

    def test_filter_set_filters_and_searches_by_supplier(self):
        filterset = WarrantyFilterSet(data={"supplier": str(self.supplier.pk)}, queryset=Warranty.objects.all())
        self.assertEqual(set(filterset.qs.values_list("pk", flat=True)), {self.warranty.pk})

        searched = WarrantyFilterSet(data={"q": "Also Deutschland"}, queryset=Warranty.objects.all())
        self.assertEqual(set(searched.qs.values_list("pk", flat=True)), {self.warranty.pk})

    def test_admin_lists_and_searches_by_supplier(self):
        model_admin = WarrantyAdmin(Warranty, AdminSite())
        self.assertIn("supplier", model_admin.list_display)
        self.assertIn("supplier__name", model_admin.search_fields)

        request = RequestFactory().get("/admin/assets/warranty/", {"q": "Also Deutschland"})
        request.user = self.tenant_admin
        results, _ = model_admin.get_search_results(request, Warranty.objects.all(), "Also Deutschland")

        self.assertIn(self.warranty.pk, set(results.values_list("pk", flat=True)))
        self.assertNotIn(self.warranty_without_supplier.pk, set(results.values_list("pk", flat=True)))


class WarrantyVendorScopeMatrixTests(TenantTestMixin, TestCase):
    """Plan section 4 (assets rows): global Suppliers, tenant-scoped asset reach."""

    def setUp(self):
        self.group = TenantGroup.objects.create(name="Issue 500 Region", slug="i500-region")
        self.tenant_a = Tenant.objects.create(name="Issue 500 A", slug="i500-a", group=self.group)
        self.tenant_a2 = Tenant.objects.create(name="Issue 500 A2", slug="i500-a2", group=self.group)
        self.tenant_b = Tenant.objects.create(name="Issue 500 B", slug="i500-b")

        self.manufacturer = Manufacturer.objects.create(name="Issue 500 Vendor", slug="i500-vendor")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Issue 500 Latitude", slug="i500-latitude"
        )
        self.asset_role = AssetRole.objects.create(name="Issue 500 Workstation", slug="i500-workstation")
        self.status = StatusLabel.objects.create(
            name="Issue 500 Ready", slug="i500-ready", type=StatusLabel.TYPE_DEPLOYABLE
        )

        self.asset_a = self.create_asset(self.tenant_a, "Issue 500 Asset A", "I500-A")
        self.asset_a2 = self.create_asset(self.tenant_a2, "Issue 500 Asset A2", "I500-A2")
        self.asset_b = self.create_asset(self.tenant_b, "Issue 500 Asset B", "I500-B")

        self.supplier = Supplier.objects.create(name="Issue 500 Supplier", slug="i500-supplier")
        self.retired_supplier = Supplier.objects.create(name="Issue 500 Retired", slug="i500-retired")
        self.retired_supplier.soft_delete()

        self.member = self.create_member("i500_member", is_staff=False)
        self.staff_member = self.create_member("i500_staff", is_staff=True)
        self.staff_without_perms = self.create_staff_without_perms()
        self.superuser = User.objects.create_superuser(
            username="i500_superuser", email="i500_superuser@example.com", password="password123"
        )

    def tearDown(self):
        self.clear_scope()

    def create_asset(self, tenant, name, asset_tag):
        return Asset.objects.create(
            name=name,
            asset_tag=asset_tag,
            asset_type=self.asset_type,
            asset_role=self.asset_role,
            status=self.status,
            tenant=tenant,
        )

    def create_member(self, username, is_staff):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="password123",
            is_staff=is_staff,
        )
        for tenant in (self.tenant_a, self.tenant_a2):
            role = Role.objects.create(
                tenant=tenant,
                name=f"Issue 500 Role {tenant.slug} {username}",
                permissions=ASSET_PERMS + WARRANTY_PERMS,
            )
            grant(user, tenant, role)
        return user

    def create_staff_without_perms(self):
        user = User.objects.create_user(
            username="i500_staff_noperm", email="i500_staff_noperm@example.com", password="password123", is_staff=True
        )
        role = Role.objects.create(tenant=self.tenant_a, name="Issue 500 Empty Role", permissions=[])
        grant(user, self.tenant_a, role)
        return user

    def clear_scope(self):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(None)
        set_current_all_accessible(False)
        _current_user.set(None)

    def activate_single_tenant(self, user=None):
        user = user or self.member
        self.set_active_tenant(self.tenant_a, Membership.objects.get(user=user, tenant=self.tenant_a))
        set_current_tenant_group(None)
        set_current_all_accessible(False)
        _current_user.set(user)

    def activate_group(self, user=None):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        set_current_all_accessible(False)
        _current_user.set(user or self.member)

    def activate_all_accessible(self, user=None):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(None)
        set_current_all_accessible(True)
        _current_user.set(user or self.member)

    def activate_global(self):
        self.clear_scope()
        _current_user.set(self.superuser)

    def warranty_form_asset_ids(self):
        return set(WarrantyForm().fields["asset"].queryset.values_list("pk", flat=True))

    def inline_supplier_ids(self):
        choices = AssetForm().fields["warranty_supplier"].queryset
        return set(choices.values_list("pk", flat=True))

    def assert_global_supplier_choices(self):
        choices = set(WarrantyForm().fields["supplier"].queryset.values_list("pk", flat=True))
        self.assertIn(self.supplier.pk, choices)
        self.assertNotIn(self.retired_supplier.pk, choices)
        self.assertEqual(self.inline_supplier_ids(), choices)

    @staticmethod
    def browser_payload(form):
        """Reproduce what the rendered form submits, so the POST is browser-faithful."""
        payload = {}
        for name, field in form.fields.items():
            if isinstance(field.widget, forms.SelectMultiple):
                payload[name] = []
                continue
            if isinstance(field.widget, forms.CheckboxInput):
                payload[name] = "on" if form[name].value() else ""
                continue
            payload[name] = field.widget.format_value(form[name].value()) or ""
        return payload

    def test_single_tenant_scope_offers_its_assets_and_every_active_supplier(self):
        for user in (self.member, self.staff_member):
            with self.subTest(is_staff=user.is_staff):
                self.activate_single_tenant(user)
                self.assertEqual(self.warranty_form_asset_ids(), {self.asset_a.pk})
                self.assert_global_supplier_choices()

    def test_single_tenant_scope_rejects_a_foreign_asset_on_submit(self):
        self.activate_single_tenant()

        rejected = WarrantyForm(data=warranty_payload(self.asset_b, supplier=self.supplier.pk))
        self.assertFalse(rejected.is_valid())
        self.assertIn("asset", rejected.errors)

        accepted = WarrantyForm(data=warranty_payload(self.asset_a, supplier=self.supplier.pk))
        self.assertTrue(accepted.is_valid(), accepted.errors)

    def test_tenant_group_scope_offers_the_group_assets_only(self):
        self.activate_group()

        self.assertEqual(self.warranty_form_asset_ids(), {self.asset_a.pk, self.asset_a2.pk})
        self.assert_global_supplier_choices()
        self.assertFalse(WarrantyForm(data=warranty_payload(self.asset_b)).is_valid())
        self.assertTrue(WarrantyForm(data=warranty_payload(self.asset_a2)).is_valid())

    def test_all_accessible_scope_offers_exactly_the_accessible_assets(self):
        self.activate_all_accessible()

        self.assertEqual(self.warranty_form_asset_ids(), {self.asset_a.pk, self.asset_a2.pk})
        self.assert_global_supplier_choices()

        rejected = WarrantyForm(data=warranty_payload(self.asset_b))
        self.assertFalse(rejected.is_valid())
        self.assertIn("asset", rejected.errors)

    def test_superuser_scope_sees_every_asset_and_supplier(self):
        self.activate_global()

        self.assertEqual(self.warranty_form_asset_ids(), {self.asset_a.pk, self.asset_a2.pk, self.asset_b.pk})
        self.assert_global_supplier_choices()

    def test_member_without_any_scope_fails_closed(self):
        stranger = User.objects.create_user(username="i500_stranger", password="password123")
        self.activate_all_accessible(stranger)

        self.assertEqual(self.warranty_form_asset_ids(), set())

    def test_single_tenant_asset_edit_page_scopes_the_warranty_supplier_select(self):
        self.client_login_to_tenant(self.member, self.tenant_a)

        response = self.client.get(reverse("assets:asset_update", kwargs={"pk": self.asset_a.pk}))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn(self.supplier.pk, set(form.fields["warranty_supplier"].queryset.values_list("pk", flat=True)))
        self.assertNotIn(
            self.retired_supplier.pk, set(form.fields["warranty_supplier"].queryset.values_list("pk", flat=True))
        )

        foreign = self.client.get(reverse("assets:asset_update", kwargs={"pk": self.asset_b.pk}))
        self.assertEqual(foreign.status_code, 404)

    def test_single_tenant_asset_edit_post_creates_the_inline_warranty(self):
        self.client_login_to_tenant(self.member, self.tenant_a)
        edit_url = reverse("assets:asset_update", kwargs={"pk": self.asset_a.pk})

        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        data = self.browser_payload(page.context["form"])
        data.update(
            {
                "warranty_supplier": str(self.supplier.pk),
                "warranty_type": WarrantyTypeChoices.HARDWARE.value,
                "warranty_start_date": TODAY.isoformat(),
                "warranty_end_date": (TODAY + ONE_YEAR).isoformat(),
            }
        )

        response = self.client.post(edit_url, data)
        self.assertEqual(response.status_code, 302)

        warranty = Warranty.objects.get(asset=self.asset_a)
        self.assertEqual(warranty.supplier_id, self.supplier.pk)

    def test_group_scope_edit_hides_the_asset_outside_the_group(self):
        self.client_login_to_tenant(self.member, self.tenant_a)

        in_group = self.client.get(
            reverse("assets:asset_update", kwargs={"pk": self.asset_a2.pk}),
            {"switch_tenant_group": self.group.pk},
        )
        self.assertEqual(in_group.status_code, 200)

        outside = self.client.get(
            reverse("assets:asset_update", kwargs={"pk": self.asset_b.pk}),
            {"switch_tenant_group": self.group.pk},
        )
        self.assertEqual(outside.status_code, 404)

    def test_all_accessible_scope_edit_hides_the_inaccessible_asset(self):
        self.client_login_to_tenant(self.member, self.tenant_a)

        accessible = self.client.get(
            reverse("assets:asset_update", kwargs={"pk": self.asset_a.pk}),
            {"switch_all_accessible": 1},
        )
        self.assertEqual(accessible.status_code, 200)

        inaccessible = self.client.get(
            reverse("assets:asset_update", kwargs={"pk": self.asset_b.pk}),
            {"switch_all_accessible": 1},
        )
        self.assertEqual(inaccessible.status_code, 404)

    def test_staff_flag_alone_does_not_open_the_warranty_or_asset_write_views(self):
        self.client_login_to_tenant(self.staff_without_perms, self.tenant_a)

        create_page = self.client.get(reverse("assets:warranty_create"))
        self.assertEqual(create_page.status_code, 403)

        edit_page = self.client.get(reverse("assets:asset_update", kwargs={"pk": self.asset_a.pk}))
        self.assertEqual(edit_page.status_code, 403)
