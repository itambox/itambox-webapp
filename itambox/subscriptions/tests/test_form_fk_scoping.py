"""B1/B2-class follow-up: SubscriptionForm.cost_center must be tenant-scoped.

Issue #500 qualifies the same treatment for the Supplier FK: the form must
rescope ``supplier.queryset`` per request and pin every scope shape (single
tenant, tenant group, All-accessible, superuser/global) with rendered choices
and bound-POST negatives.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from assets.models import Supplier
from core.managers import set_current_all_accessible, set_current_tenant_group
from core.tests.mixins import TenantTestMixin, grant
from itambox.middleware import set_current_user
from organization.models import CostCenter, Membership, Role, Tenant, TenantGroup
from subscriptions.forms import SubscriptionBulkEditForm, SubscriptionBulkImportForm, SubscriptionForm
from subscriptions.models import Subscription, SubscriptionTypeChoices

User = get_user_model()


class SubscriptionFormFkScopingTests(TenantTestMixin, TestCase):
    def test_form_exposes_canonical_terms_but_not_lifecycle_fields(self):
        fields = SubscriptionForm().fields
        self.assertIn("vendor_contract_auto_renews", fields)
        self.assertNotIn("auto_renewal", fields)
        self.assertNotIn("status", fields)
        self.assertNotIn("cancellation_date", fields)

        bulk_fields = SubscriptionBulkEditForm(model=Subscription).fields
        self.assertNotIn("status", bulk_fields)
        self.assertNotIn("cancellation_date", bulk_fields)

    def test_import_form_maps_legacy_and_canonical_renewal_terms_without_lifecycle_fields(self):
        form = SubscriptionBulkImportForm()
        self.assertNotIn("status", form.field_names)
        self.assertNotIn("cancellation_date", form.field_names)
        self.assertEqual(
            form.map_row({"auto_renewal": "false"})["vendor_contract_auto_renews"],
            False,
        )
        self.assertEqual(
            form.map_row({"vendor_contract_auto_renews": "yes"})["vendor_contract_auto_renews"],
            True,
        )
        with self.assertRaisesMessage(Exception, "conflicts"):
            form.map_row({"auto_renewal": "false", "vendor_contract_auto_renews": "true"})

    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="sffk-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="sffk-b")
        self.cc_a = baker.make(CostCenter, tenant=self.tenant)
        self.cc_b = baker.make(CostCenter, tenant=self.tenant_b)
        self.supplier_a = Supplier.objects.create(name="Supplier A", tenant=self.tenant)
        self.supplier_b = Supplier.objects.create(name="Supplier B", tenant=self.tenant_b)
        self.global_supplier = Supplier.objects.create(name="Global Supplier")
        self.inactive_supplier = Supplier.objects.create(
            name="Retired Supplier A",
            tenant=self.tenant,
            is_active=False,
        )
        self.set_active_tenant(self.tenant)

    def tearDown(self):
        set_current_user(None)
        self.clear_tenant_context()

    def _supplier_pks(self):
        return set(SubscriptionForm().fields["supplier"].queryset.values_list("pk", flat=True))

    def _bound_supplier_errors(self, supplier_pk):
        form = SubscriptionForm(
            data={
                "name": "Scope Probe",
                "supplier": supplier_pk,
                "type": SubscriptionTypeChoices.SAAS,
            }
        )
        self.assertFalse(form.is_valid())
        return form.errors["supplier"]

    def test_cost_center_scoped_to_tenant(self):
        pks = set(SubscriptionForm().fields["cost_center"].queryset.values_list("pk", flat=True))
        self.assertIn(self.cc_a.pk, pks)
        self.assertNotIn(self.cc_b.pk, pks)

    def test_supplier_choices_are_tenant_scoped_for_members_and_staff(self):
        for is_staff in (False, True):
            with self.subTest(is_staff=is_staff):
                self.tenant_user.is_staff = is_staff
                self.tenant_user.save(update_fields=["is_staff"])

                pks = self._supplier_pks()

                self.assertIn(self.supplier_a.pk, pks)
                self.assertIn(self.global_supplier.pk, pks)
                self.assertNotIn(self.supplier_b.pk, pks)
                self.assertNotIn(self.inactive_supplier.pk, pks)

                self.assertIn("not one of the available choices", str(self._bound_supplier_errors(self.supplier_b.pk)))

    def test_supplier_choices_follow_the_tenant_group_subtree(self):
        group = TenantGroup.objects.create(name="SFFK Group", slug="sffk-group")
        child_group = TenantGroup.objects.create(name="SFFK Child Group", slug="sffk-child", parent=group)
        self.tenant.group = group
        self.tenant.save(update_fields=["group"])
        child_tenant = Tenant.objects.create(name="SFFK Child Tenant", slug="sffk-child-tenant", group=child_group)
        outside_group = Tenant.objects.create(name="SFFK Outside", slug="sffk-outside")
        supplier_child = Supplier.objects.create(name="Child Tenant Supplier", tenant=child_tenant)
        supplier_outside = Supplier.objects.create(name="Outside Group Supplier", tenant=outside_group)
        # Group scope intersects the accessible set with the subtree: reach into
        # the child tenant makes its supplier part of the scoped choices.
        child_role = Role.objects.create(tenant=child_tenant, name="SFFK Child Role", permissions=[])
        grant(self.tenant_user, child_tenant, child_role)

        set_current_user(self.tenant_user)
        self.clear_tenant_context()
        set_current_tenant_group(group)
        try:
            pks = self._supplier_pks()
            errors = self._bound_supplier_errors(supplier_outside.pk)
        finally:
            set_current_tenant_group(None)
            set_current_user(None)
            self.set_active_tenant(self.tenant)

        self.assertIn(self.supplier_a.pk, pks)
        self.assertIn(supplier_child.pk, pks)
        self.assertIn(self.global_supplier.pk, pks)
        self.assertNotIn(supplier_outside.pk, pks)
        self.assertNotIn(self.supplier_b.pk, pks)
        self.assertIn("not one of the available choices", str(errors))

    def test_supplier_choices_follow_the_all_accessible_scope(self):
        second_tenant = Tenant.objects.create(name="SFFK Second Tenant", slug="sffk-second")
        second_role = Role.objects.create(tenant=second_tenant, name="SFFK Second Role", permissions=[])
        grant(self.tenant_user, second_tenant, second_role)
        supplier_second = Supplier.objects.create(name="Second Tenant Supplier", tenant=second_tenant)

        set_current_user(self.tenant_user)
        self.clear_tenant_context()
        set_current_all_accessible(True)
        try:
            pks = self._supplier_pks()
            errors = self._bound_supplier_errors(self.supplier_b.pk)
        finally:
            set_current_all_accessible(False)
            set_current_user(None)
            self.set_active_tenant(self.tenant)

        self.assertIn(self.supplier_a.pk, pks)
        self.assertIn(supplier_second.pk, pks)
        self.assertIn(self.global_supplier.pk, pks)
        self.assertNotIn(self.supplier_b.pk, pks)
        self.assertIn("not one of the available choices", str(errors))

    def test_superuser_sees_every_supplier_and_scope_less_members_see_none(self):
        set_current_user(self.tenant_admin)
        self.clear_tenant_context()
        try:
            superuser_pks = self._supplier_pks()
        finally:
            set_current_user(None)
            self.set_active_tenant(self.tenant)

        self.assertIn(self.supplier_a.pk, superuser_pks)
        self.assertIn(self.supplier_b.pk, superuser_pks)
        self.assertIn(self.global_supplier.pk, superuser_pks)
        self.assertNotIn(self.inactive_supplier.pk, superuser_pks)

        # A member with no resolved scope fails closed instead of falling back
        # to the unscoped queryset.
        set_current_user(self.tenant_user)
        self.clear_tenant_context()
        try:
            self.assertEqual(self._supplier_pks(), set())
        finally:
            set_current_user(None)
            self.set_active_tenant(self.tenant)

    def test_create_view_denies_a_staff_member_without_permissions(self):
        staff_member = User.objects.create_user(username="sffk-staff", password="password", is_staff=True)
        Membership.objects.create(user=staff_member, tenant=self.tenant)

        self.client_login_to_tenant(staff_member, self.tenant)
        response = self.client.get(reverse("subscriptions:subscription_create"))

        self.assertEqual(response.status_code, 403)
