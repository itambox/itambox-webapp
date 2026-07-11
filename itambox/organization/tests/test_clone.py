from django.test import TestCase
from django.contrib.auth import get_user_model

from organization.models import Tenant, Role
from organization.forms import RoleForm as TenantRoleForm
from organization.views.role_views import RoleCloneView as TenantRoleCloneView
from core.managers import set_current_tenant, set_current_membership

User = get_user_model()


class TenantRoleCloneTests(TestCase):
    """The Role clone flow supports copying an existing role's permission set into a
    fresh (unsaved) instance with its tenant cleared. Post RBAC-collapse ``RoleForm``
    has no tenant picker (``scratch/RBAC_STAGE2_SPEC.md`` §7): the owner is always
    resolved from context — a ``?tenant=`` deep-link kwarg, else the active tenant —
    so "cloning into a chosen tenant" now means switching the active tenant context
    before opening/submitting the clone form."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.superuser = User.objects.create_superuser(
            username='super', email='super@example.com', password='pw'
        )
        self.source = Role.objects.create(
            tenant=self.tenant_a,
            name="Inventory Manager",
            permissions=["assets.view_asset", "assets.add_asset"],
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_str_is_null_safe_without_tenant(self):
        # An unsaved clone awaiting a tenant assignment must not crash on str().
        self.assertEqual(str(Role(name="Orphan")), "Orphan")

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
        self.assertEqual(Role.objects.filter(name="Inventory Manager (Copy)").count(), 0)

    def test_form_without_tenant_context_rejects_clone(self):
        # No `tenant` kwarg and no active tenant → RoleForm has no picker to fall
        # back on (it was deleted with the Provider collapse), so it fails closed.
        clone = self.source.clone()
        clone.name = "Inventory Manager (Copy)"
        clone.tenant = None

        form = TenantRoleForm(
            data={'name': "Inventory Manager (Copy)", 'description': ''},
            instance=clone,
            user=self.superuser,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("No tenant context", str(form.errors))

    def test_form_on_clone_prechecks_permissions_from_active_tenant_context(self):
        set_current_tenant(self.tenant_a)
        clone = self.source.clone()
        clone.name = "Inventory Manager (Copy)"
        clone.tenant = None

        # No `tenant` kwarg → the form falls back to the active tenant context
        # rather than rendering a picker (there is none post-collapse).
        form = TenantRoleForm(instance=clone, user=self.superuser)
        self.assertNotIn('tenant', form.fields)
        self.assertEqual(form.owner_tenant, self.tenant_a)

        # Permission matrix pre-checked from the cloned permission set.
        self.assertTrue(form.fields['perm_asset_read'].initial)
        self.assertTrue(form.fields['perm_asset_create'].initial)
        self.assertFalse(form.fields['perm_asset_edit'].initial)

    def test_clone_saved_into_active_tenant_context(self):
        # "Chosen tenant" is now expressed by switching the active tenant context
        # before submitting the clone form — there is no in-form tenant picker.
        set_current_tenant(self.tenant_b)
        clone = self.source.clone()
        clone.name = "Inventory Manager (Copy)"
        clone.tenant = None

        form = TenantRoleForm(
            data={
                'name': "Inventory Manager (Copy)",
                'description': '',
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
