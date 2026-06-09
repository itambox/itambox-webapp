from django.test import TestCase
from django.contrib.auth import get_user_model

from organization.models import Tenant, TenantRole
from organization.forms import TenantRoleForm
from organization.views.tenantrole_views import TenantRoleCloneView
from core.managers import set_current_tenant, set_current_membership

User = get_user_model()


class TenantRoleCloneTests(TestCase):
    """The TenantRole clone flow supports onboarding a new tenant with an
    existing role's permission set: the clone is created unsaved with its tenant
    cleared, and the admin assigns a target tenant on the form."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.superuser = User.objects.create_superuser(
            username='super', email='super@example.com', password='pw'
        )
        self.source = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Inventory Manager",
            permissions=["assets.view_asset", "assets.add_asset"],
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_str_is_null_safe_without_tenant(self):
        # An unsaved clone awaiting a tenant assignment must not crash on str().
        self.assertEqual(str(TenantRole(name="Orphan")), "Orphan")

    def test_clone_view_builds_unsaved_tenantless_copy(self):
        set_current_tenant(self.tenant_a)
        view = TenantRoleCloneView()
        view.kwargs = {'pk': self.source.pk}

        clone = view.get_object()

        # Not persisted, tenant cleared, name suffixed, permissions carried over.
        self.assertIsNone(clone.pk)
        self.assertIsNone(clone.tenant_id)
        self.assertEqual(clone.name, "Inventory Manager (Copy)")
        self.assertEqual(clone.permissions, ["assets.view_asset", "assets.add_asset"])
        # Nothing new was written to the DB on GET.
        self.assertEqual(TenantRole.objects.filter(name="Inventory Manager (Copy)").count(), 0)

    def test_form_on_clone_requires_tenant_and_prechecks_permissions(self):
        clone = self.source.clone()
        clone.name = "Inventory Manager (Copy)"
        clone.tenant = None

        # No `tenant` kwarg → form must render a required, full tenant picker.
        form = TenantRoleForm(instance=clone, user=self.superuser)
        self.assertTrue(form.fields['tenant'].required)
        self.assertEqual(form.fields['tenant'].queryset.count(), Tenant.objects.count())

        # Permission matrix pre-checked from the cloned permission set.
        self.assertTrue(form.fields['perm_asset_read'].initial)
        self.assertTrue(form.fields['perm_asset_create'].initial)
        self.assertFalse(form.fields['perm_asset_edit'].initial)

    def test_clone_saved_into_chosen_tenant(self):
        clone = self.source.clone()
        clone.name = "Inventory Manager (Copy)"
        clone.tenant = None

        form = TenantRoleForm(
            data={
                'name': "Inventory Manager (Copy)",
                'description': '',
                'tenant': self.tenant_b.pk,
                'perm_asset_read': True,
                'perm_asset_create': True,
            },
            instance=clone,
            user=self.superuser,
        )
        self.assertTrue(form.is_valid(), form.errors)
        new_role = form.save()

        # A brand-new role landed in the chosen (different) tenant.
        self.assertIsNotNone(new_role.pk)
        self.assertNotEqual(new_role.pk, self.source.pk)
        self.assertEqual(new_role.tenant, self.tenant_b)
        self.assertIn('assets.view_asset', new_role.permissions)
        self.assertIn('assets.add_asset', new_role.permissions)
        # Original is untouched.
        self.source.refresh_from_db()
        self.assertEqual(self.source.tenant, self.tenant_a)
