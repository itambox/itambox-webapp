"""Issue #500: commercial vocabulary on the procurement surfaces.

Pins the exclusive Contract/Subscription ownership mapping on
``ContractForm.contract_type`` and qualifies the Contract form's vendor and
asset scopes for members and staff across all four scope shapes.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from assets.models import Asset, Supplier
from core.managers import set_current_all_accessible, set_current_tenant_group
from core.tests.mixins import TenantTestMixin, grant
from itambox.middleware import set_current_user
from organization.models import Membership, Role, Tenant, TenantGroup
from procurement.forms import AGREEMENT_OWNERSHIP_HELP, ContractForm
from procurement.models import ContractStatusChoices, ContractTypeChoices

User = get_user_model()

AGREEMENT_OWNERSHIP_MAPPING = (
    "Record one agreement in one module only. SaaS and cloud entitlement: record as Subscription. "
    "Support, maintenance, lease, warranty, SLA, or asset-covered service: record as Contract. "
    "Other recurring entitlement without asset/SLA coverage: record as Subscription. "
    "Other legal/commercial agreement: record as Contract."
)


class ContractFormCommercialVocabularyTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="pcv-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="pcv-b")
        self.supplier_a = Supplier.objects.create(name="Vendor Alpha", slug="pcv-alpha")
        self.supplier_b = Supplier.objects.create(name="Vendor Bravo", slug="pcv-bravo")
        self.asset_a = baker.make(Asset, name="PCV Asset A", tenant=self.tenant)
        self.asset_b = baker.make(Asset, name="PCV Asset B", tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def tearDown(self):
        set_current_user(None)
        self.clear_tenant_context()

    def _supplier_pks(self):
        return set(ContractForm().fields["supplier"].queryset.values_list("pk", flat=True))

    def _asset_pks(self):
        return set(ContractForm().fields["assets"].queryset.values_list("pk", flat=True))

    def _contract_data(self, **overrides):
        data = {
            "name": "Covered Service Agreement",
            "contract_number": "PCV-001",
            "contract_type": ContractTypeChoices.SUPPORT,
            "status": ContractStatusChoices.ACTIVE,
            "start_date": "2026-01-01",
            "end_date": "2027-01-01",
            "currency": "EUR",
        }
        data.update(overrides)
        return data

    def test_supplier_choices_stay_global_while_assets_follow_the_tenant(self):
        expected_suppliers = {self.supplier_a.pk, self.supplier_b.pk}

        for is_staff in (False, True):
            with self.subTest(is_staff=is_staff):
                self.tenant_user.is_staff = is_staff
                self.tenant_user.save(update_fields=["is_staff"])

                self.assertEqual(self._supplier_pks(), expected_suppliers)

                asset_pks = self._asset_pks()
                self.assertIn(self.asset_a.pk, asset_pks)
                self.assertNotIn(self.asset_b.pk, asset_pks)

        form = ContractForm(data=self._contract_data(assets=[self.asset_b.pk], supplier=self.supplier_a.pk))
        self.assertFalse(form.is_valid())
        self.assertIn("assets", form.errors)
        self.assertIn("not one of the available choices", str(form.errors["assets"]))

    def test_supplier_choices_stay_global_under_a_tenant_group_scope(self):
        group = TenantGroup.objects.create(name="PCV Group", slug="pcv-group")
        child_group = TenantGroup.objects.create(name="PCV Child Group", slug="pcv-child", parent=group)
        self.tenant.group = group
        self.tenant.save(update_fields=["group"])
        child_tenant = Tenant.objects.create(name="PCV Child Tenant", slug="pcv-child-tenant", group=child_group)
        outside_tenant = Tenant.objects.create(name="PCV Outside", slug="pcv-outside")
        asset_child = baker.make(Asset, name="PCV Child Asset", tenant=child_tenant)
        asset_outside = baker.make(Asset, name="PCV Outside Asset", tenant=outside_tenant)
        # Group scope intersects the accessible set with the subtree: reach into
        # the child tenant makes its asset part of the scoped choices.
        child_role = Role.objects.create(tenant=child_tenant, name="PCV Child Role", permissions=[])
        grant(self.tenant_user, child_tenant, child_role)

        set_current_user(self.tenant_user)
        self.clear_tenant_context()
        set_current_tenant_group(group)
        try:
            supplier_pks = self._supplier_pks()
            asset_pks = self._asset_pks()
            form = ContractForm(data=self._contract_data(assets=[asset_outside.pk]))
            self.assertFalse(form.is_valid())
            errors = form.errors["assets"]
        finally:
            set_current_tenant_group(None)
            set_current_user(None)
            self.set_active_tenant(self.tenant)

        self.assertEqual(supplier_pks, {self.supplier_a.pk, self.supplier_b.pk})
        self.assertIn(self.asset_a.pk, asset_pks)
        self.assertIn(asset_child.pk, asset_pks)
        self.assertNotIn(asset_outside.pk, asset_pks)
        self.assertNotIn(self.asset_b.pk, asset_pks)
        self.assertIn("not one of the available choices", str(errors))

    def test_assets_are_limited_to_the_all_accessible_tenants(self):
        second_tenant = Tenant.objects.create(name="PCV Second Tenant", slug="pcv-second")
        second_role = Role.objects.create(tenant=second_tenant, name="PCV Second Role", permissions=[])
        grant(self.tenant_user, second_tenant, second_role)
        asset_second = baker.make(Asset, name="PCV Second Asset", tenant=second_tenant)

        set_current_user(self.tenant_user)
        self.clear_tenant_context()
        set_current_all_accessible(True)
        try:
            supplier_pks = self._supplier_pks()
            asset_pks = self._asset_pks()
            form = ContractForm(data=self._contract_data(assets=[self.asset_b.pk]))
            self.assertFalse(form.is_valid())
            errors = form.errors["assets"]
        finally:
            set_current_all_accessible(False)
            set_current_user(None)
            self.set_active_tenant(self.tenant)

        self.assertEqual(supplier_pks, {self.supplier_a.pk, self.supplier_b.pk})
        self.assertIn(self.asset_a.pk, asset_pks)
        self.assertIn(asset_second.pk, asset_pks)
        self.assertNotIn(self.asset_b.pk, asset_pks)
        self.assertIn("not one of the available choices", str(errors))

    def test_superuser_sees_every_tenant_and_scope_less_members_fail_closed(self):
        set_current_user(self.tenant_admin)
        self.clear_tenant_context()
        try:
            supplier_pks = self._supplier_pks()
            asset_pks = self._asset_pks()
        finally:
            set_current_user(None)
            self.set_active_tenant(self.tenant)

        self.assertEqual(supplier_pks, {self.supplier_a.pk, self.supplier_b.pk})
        self.assertIn(self.asset_a.pk, asset_pks)
        self.assertIn(self.asset_b.pk, asset_pks)

        set_current_user(self.tenant_user)
        self.clear_tenant_context()
        try:
            self.assertEqual(self._asset_pks(), set())
            # Global reference data is not tenant-scoped, so it stays complete.
            self.assertEqual(self._supplier_pks(), {self.supplier_a.pk, self.supplier_b.pk})
        finally:
            set_current_user(None)
            self.set_active_tenant(self.tenant)

    def test_contract_type_help_carries_the_exclusive_mapping(self):
        self.assertEqual(str(AGREEMENT_OWNERSHIP_HELP), AGREEMENT_OWNERSHIP_MAPPING)
        self.assertEqual(str(ContractForm().fields["contract_type"].help_text), AGREEMENT_OWNERSHIP_MAPPING)

    def test_contract_type_choices_are_unchanged(self):
        expected = [(value, str(label)) for value, label in ContractTypeChoices.choices]

        self.assertEqual(
            [(value, str(label)) for value, label in ContractForm().fields["contract_type"].choices],
            expected,
        )

    def test_contract_surfaces_expose_no_internal_subscription_link(self):
        field_names = set(ContractForm().fields)

        self.assertEqual({name for name in field_names if "subscription" in name}, set())

    def test_create_view_denies_a_staff_member_without_permissions(self):
        staff_member = User.objects.create_user(username="pcv-staff", password="password", is_staff=True)
        Membership.objects.create(user=staff_member, tenant=self.tenant)

        self.client_login_to_tenant(staff_member, self.tenant)
        response = self.client.get(reverse("procurement:contract_create"))

        self.assertEqual(response.status_code, 403)
