from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.contrib.auth import get_user_model
from organization.models import Tenant, TenantMembership, TenantRole
from organization.forms import TenantRoleForm
from core.managers import set_current_tenant, set_current_membership

User = get_user_model()

class TenantRoleSecurityTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        
        self.super_user = User.objects.create_superuser(
            username='superuser', email='super@example.com', password='password123'
        )
        self.user_a = User.objects.create_user(
            username='usera', email='usera@example.com', password='password123'
        )
        self.user_b = User.objects.create_user(
            username='userb', email='userb@example.com', password='password123'
        )

    def test_role_scoping_to_tenant(self):
        # Create role in Tenant A
        role_a = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Alpha Admin",
            permissions=["assets.view_asset"]
        )
        
        # Scoped to Tenant A
        set_current_tenant(self.tenant_a)
        self.assertIn(role_a, TenantRole.objects.all())
        
        # Scoped to Tenant B (should be invisible)
        set_current_tenant(self.tenant_b)
        self.assertNotIn(role_a, TenantRole.objects.all())
        
        # Reset context
        set_current_tenant(None)

    def test_form_serialization_and_deserialization(self):
        # Create role using TenantRoleForm
        form_data = {
            'name': 'Custom Asset Specialist',
            'description': 'Can view and change assets',
            'perm_asset_read': True,
            'perm_asset_create': True,
            'perm_asset_edit': True,
            'perm_asset_delete': False,
        }
        
        form = TenantRoleForm(data=form_data, tenant=self.tenant_a, user=self.super_user)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        
        # Verify packed permissions list
        self.assertIn('assets.view_asset', role.permissions)
        self.assertIn('assets.add_asset', role.permissions)
        self.assertIn('assets.change_asset', role.permissions)
        self.assertNotIn('assets.delete_asset', role.permissions)
        # Dashboard permissions are automatically added
        self.assertIn('extras.view_dashboard', role.permissions)
        
        # Verify deserialization into form initial values
        edit_form = TenantRoleForm(instance=role, tenant=self.tenant_a, user=self.super_user)
        self.assertTrue(edit_form.fields['perm_asset_read'].initial)
        self.assertTrue(edit_form.fields['perm_asset_create'].initial)
        self.assertTrue(edit_form.fields['perm_asset_edit'].initial)
        self.assertFalse(edit_form.fields['perm_asset_delete'].initial)

    def test_permission_backend_resolution(self):
        role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="ReadOnly Member",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        membership = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
            role=role
        )
        
        set_current_membership(membership)
        set_current_tenant(self.tenant_a)
        
        self.assertTrue(self.user_a.has_perm('assets.view_asset'))
        self.assertFalse(self.user_a.has_perm('assets.add_asset'))
        self.assertFalse(self.user_a.has_perm('assets.delete_asset'))
        
        set_current_membership(None)
        set_current_tenant(None)

    def test_privilege_escalation_validation(self):
        # User A is a ReadOnly member
        reader_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        membership_a = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
            role=reader_role
        )
        
        set_current_membership(membership_a)
        set_current_tenant(self.tenant_a)
        
        # User A tries to create a role with Delete Asset permission (Privilege Escalation!)
        form_data = {
            'name': 'Rogue Admin',
            'description': 'Elevated permissions',
            'perm_asset_read': True,
            'perm_asset_delete': True, # User A does not have delete_asset!
        }
        
        form = TenantRoleForm(data=form_data, tenant=self.tenant_a, user=self.user_a)
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)
        self.assertTrue(any("Privilege escalation detected" in e for e in form.errors['__all__']))
        
        set_current_membership(None)
        set_current_tenant(None)

    def test_invalid_codename_whitelist_validation(self):
        # Form clean handles parsing, but let's test invalid permissions by patching MATRIX_MODELS in test
        from organization.forms.tenantrole_form import MATRIX_MODELS
        
        # Add an invalid mock model definition to MATRIX_MODELS
        MATRIX_MODELS['mock_invalid'] = {
            'label': 'Invalid Model',
            'app': 'nonexistentapp',
            'model_name': 'do_magic'
        }
        
        try:
            form_data = {
                'name': 'Magician',
                'description': 'Uses nonexistent permissions',
                'perm_mock_invalid_read': True,
            }
            form = TenantRoleForm(data=form_data, tenant=self.tenant_a, user=self.super_user)
            self.assertFalse(form.is_valid())
            self.assertIn('__all__', form.errors)
            self.assertTrue(any("Invalid permission codenames" in e for e in form.errors['__all__']))
        finally:
            del MATRIX_MODELS['mock_invalid']

    def test_role_deletion_protection(self):
        role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Deletable?",
            permissions=["assets.view_asset"]
        )
        TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
            role=role
        )
        
        # Since membership.role on_delete=models.PROTECT, we should get a ProtectedError on deletion
        with self.assertRaises(ProtectedError):
            role.delete()

    def test_global_mode_tenant_selection(self):
        # In global mode (no tenant in kwargs), tenant is selected in form fields
        form_data = {
            'name': 'Global Role',
            'tenant': self.tenant_b.pk,
            'description': 'Created in global mode',
            'perm_asset_read': True,
        }
        form = TenantRoleForm(data=form_data, user=self.super_user)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.tenant, self.tenant_b)
