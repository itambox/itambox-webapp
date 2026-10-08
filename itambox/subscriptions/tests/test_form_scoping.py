"""Explicit scoping declarations of the subscriptions forms (#584, WP3).

The subscriptions forms declare tenant scoping, tenant requiredness and
TomSelect behaviour through ``TenantScopedFormMixin`` instead of relying on the
global form patches. These assertions pin each declaration directly so they
stay true when the patches are removed.
"""

from django.test import TestCase
from model_bakery import baker

from assets.models import Asset
from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.models import CostCenter, Location, Tenant
from subscriptions.forms import SubscriptionAssignmentForm, SubscriptionCheckoutForm, SubscriptionForm

FORMS = (SubscriptionForm, SubscriptionAssignmentForm, SubscriptionCheckoutForm)


class SubscriptionFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="sub-fs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="sub-fs-b")
        self.cc_a = baker.make(CostCenter, tenant=self.tenant)
        self.cc_b = baker.make(CostCenter, tenant=self.tenant_b)
        self.loc_a = baker.make(Location, tenant=self.tenant)
        self.loc_b = baker.make(Location, tenant=self.tenant_b)
        self.asset_b = baker.make(Asset, tenant=self.tenant_b)
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

    def test_subscription_form_declares_tenant_behaviour(self):
        self.assertIs(SubscriptionForm.tenant_required, True)
        self.assertIs(SubscriptionForm.tenant_autoset_when_single, True)
        self.assertIs(SubscriptionForm().fields["tenant"].required, True)

    def test_model_choice_fields_are_scoped_explicitly(self):
        expected = (
            (SubscriptionForm, ("tenant", "supplier", "cost_center", "linked_contract")),
            (SubscriptionAssignmentForm, ("subscription",)),
            (SubscriptionCheckoutForm, ("assigned_holder", "asset", "location")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_pickers_follow_the_active_tenant(self):
        form = SubscriptionForm()
        self.assertIn(self.cc_a.pk, self._pks(form, "cost_center"))
        self.assertNotIn(self.cc_b.pk, self._pks(form, "cost_center"))
        checkout = SubscriptionCheckoutForm()
        self.assertIn(self.loc_a.pk, self._pks(checkout, "location"))
        self.assertNotIn(self.loc_b.pk, self._pks(checkout, "location"))

    def test_bound_foreign_object_is_rejected(self):
        form = SubscriptionCheckoutForm(
            data={"target_type": "location", "location": self.loc_b.pk, "asset": self.asset_b.pk}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("location", form.errors)
        self.assertIn("asset", form.errors)

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = (
            (SubscriptionForm, ("supplier", "cost_center", "linked_contract", "owner", "tenant", "tags")),
            (SubscriptionAssignmentForm, ("subscription",)),
            (SubscriptionCheckoutForm, ("target_type", "assigned_holder", "asset", "location")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)
