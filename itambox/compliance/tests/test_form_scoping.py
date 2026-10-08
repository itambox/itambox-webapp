"""Explicit scoping declarations of the compliance forms (#584, WP3).

The compliance forms declare tenant scoping, tenant requiredness and TomSelect
behaviour through ``TenantScopedFormMixin`` instead of relying on the global
form patches. These assertions pin each declaration directly so they stay true
when the patches are removed.
"""

from django.test import TestCase
from model_bakery import baker

from assets.models import StatusLabel
from compliance.forms import AssetMaintenanceForm, CustodyTemplateForm
from compliance.forms_audit import AssetAuditForm, AuditSessionForm
from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.models import Location, Site, Tenant

FORMS = (AssetMaintenanceForm, CustodyTemplateForm, AssetAuditForm, AuditSessionForm)


class ComplianceFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="cfs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="cfs-b")
        self.site = Site.objects.create(name="HQ", slug="cfs-hq")
        self.loc_a = Location.objects.create(name="Loc A", slug="cfs-loc-a", site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name="Loc B", slug="cfs-loc-b", site=self.site, tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def tearDown(self):
        set_current_user(None)
        set_current_tenant(None)
        self.clear_tenant_context()

    def test_forms_use_the_explicit_mixin(self):
        for form_class in FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertTrue(issubclass(form_class, TenantScopedFormMixin))

    def test_model_choice_fields_are_scoped_explicitly(self):
        for form_class, names in (
            (AssetMaintenanceForm, ("asset", "loaner_asset")),
            (AssetAuditForm, ("location",)),
            (AuditSessionForm, ("tenant", "location")),
        ):
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_locations_follow_the_active_tenant(self):
        for form_class in (AssetAuditForm, AuditSessionForm):
            pks = set(form_class().fields["location"].queryset.values_list("pk", flat=True))
            with self.subTest(form=form_class.__name__):
                self.assertEqual(pks, {self.loc_a.pk})

    def test_tenant_stays_optional_and_visible(self):
        for form_class in (CustodyTemplateForm, AuditSessionForm):
            field = form_class().fields["tenant"]
            with self.subTest(form=form_class.__name__):
                self.assertFalse(field.required)
                self.assertFalse(field.disabled)

    def test_status_label_choices_stay_global(self):
        baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.assertTrue(AssetAuditForm().fields["status"].queryset.exists())

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = {
            AssetMaintenanceForm: ("asset", "supplier", "loaner_asset", "repair_action"),
            CustodyTemplateForm: ("tenant", "tenant_group", "category", "signature_provider"),
            AssetAuditForm: ("location", "status"),
            AuditSessionForm: ("tenant", "location"),
        }
        for form_class, names in expected.items():
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)
