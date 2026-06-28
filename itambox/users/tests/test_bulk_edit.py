from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from model_bakery import baker

from organization.models import Tenant, Role, Membership
from core.models import ObjectChange

User = get_user_model()


class UserBulkEditTests(TestCase):
    def setUp(self):
        # Create Tenants
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        # Superuser
        self.superuser = User.objects.create_superuser(
            username='super_admin', email='super@admin.com', password='password123'
        )

        # Tenant A Admin
        self.admin_a = User.objects.create_user(
            username='admin_a', email='admin_a@test.com', password='password123', is_staff=True
        )
        self.role_a = Role.objects.create(
            tenant=self.tenant_a,
            name='Admin',
            permissions=['users.view_user', 'users.change_user'],
        )
        m = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.admin_a, tenant=self.tenant_a)
        m.roles.add(self.role_a)

        # Users belonging to Tenant A
        self.user_a1 = User.objects.create_user(
            username='user_a1', email='a1@test.com', password='password123', is_active=True
        )
        m = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.user_a1, tenant=self.tenant_a)
        m.roles.add(self.role_a)

        self.user_a2 = User.objects.create_user(
            username='user_a2', email='a2@test.com', password='password123', is_active=True
        )
        m = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.user_a2, tenant=self.tenant_a)
        m.roles.add(self.role_a)

        # User belonging to Tenant B (cross-tenant)
        self.user_b1 = User.objects.create_user(
            username='user_b1', email='b1@test.com', password='password123', is_active=True
        )
        self.role_b = Role.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=['users.view_user', 'users.change_user'],
        )
        m = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.user_b1, tenant=self.tenant_b)
        m.roles.add(self.role_b)

        # URLs
        self.bulk_edit_url = reverse('users:user_bulk_edit')
        self.list_url = reverse('users:user_list')

    def test_superuser_bulk_edit_is_active(self):
        self.client.force_login(self.superuser)

        # Set is_active=False for user_a1 and user_a2
        data = {
            'pk': [self.user_a1.pk, self.user_a2.pk],
            '_selected_fields': ['is_active'],
            'is_active': 'False',
            '_apply': 'true',
        }
        response = self.client.post(self.bulk_edit_url, data)
        self.assertEqual(response.status_code, 302)

        self.user_a1.refresh_from_db()
        self.user_a2.refresh_from_db()
        self.user_b1.refresh_from_db()

        self.assertFalse(self.user_a1.is_active)
        self.assertFalse(self.user_a2.is_active)
        self.assertTrue(self.user_b1.is_active)  # Unselected stays untouched

        # Verify ObjectChange logging
        changes = ObjectChange.objects.filter(changed_object_id=self.user_a1.pk)
        self.assertTrue(changes.exists())

    def test_bulk_edit_no_change_field(self):
        self.client.force_login(self.superuser)

        # Even if we select is_active for edit, if it's not checked in _selected_fields, it's not changed.
        # Here we only select '_selected_fields' but don't include is_staff.
        data = {
            'pk': [self.user_a1.pk],
            '_selected_fields': ['is_active'],
            'is_active': 'True',
            'is_staff': 'True',  # Not in _selected_fields
            '_apply': 'true',
        }
        response = self.client.post(self.bulk_edit_url, data)
        self.assertEqual(response.status_code, 302)

        self.user_a1.refresh_from_db()
        self.assertFalse(self.user_a1.is_staff)  # Stays False

    def test_non_superuser_cannot_grant_staff_or_superuser(self):
        self.client.force_login(self.admin_a)
        
        # Set active tenant in session
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # Try to make user_a1 a superuser
        data = {
            'pk': [self.user_a1.pk],
            '_selected_fields': ['is_superuser'],
            'is_superuser': 'True',
            '_apply': 'true',
        }
        response = self.client.post(self.bulk_edit_url, data)
        # Form should be invalid and render error page
        self.assertEqual(response.status_code, 200)
        self.assertIn("Only superusers can grant or modify staff, superuser, or login status.", response.content.decode('utf-8'))

        self.user_a1.refresh_from_db()
        self.assertFalse(self.user_a1.is_superuser)

    def test_non_superuser_tenant_boundary(self):
        self.client.force_login(self.admin_a)
        
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # Try to deactivate user_a1 (Tenant A) and user_b1 (Tenant B)
        data = {
            'pk': [self.user_a1.pk, self.user_b1.pk],
            '_selected_fields': ['is_active'],
            'is_active': 'False',
            '_apply': 'true',
        }
        response = self.client.post(self.bulk_edit_url, data)
        self.assertEqual(response.status_code, 302)

        self.user_a1.refresh_from_db()
        self.user_b1.refresh_from_db()

        self.assertFalse(self.user_a1.is_active)  # Scoped, successfully changed
        self.assertTrue(self.user_b1.is_active)  # Cross-tenant, filtered out and untouched!

    def test_self_lockout_guard_active(self):
        self.client.force_login(self.admin_a)
        
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # admin_a tries to deactivate themselves
        data = {
            'pk': [self.admin_a.pk],
            '_selected_fields': ['is_active'],
            'is_active': 'False',
            '_apply': 'true',
        }
        response = self.client.post(self.bulk_edit_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("You cannot deactivate your own user account in a bulk edit operation.", response.content.decode('utf-8'))

        self.admin_a.refresh_from_db()
        self.assertTrue(self.admin_a.is_active)

    def test_self_lockout_guard_superuser(self):
        self.client.force_login(self.superuser)

        # superuser tries to revoke their own superuser status
        data = {
            'pk': [self.superuser.pk],
            '_selected_fields': ['is_superuser'],
            'is_superuser': 'False',
            '_apply': 'true',
        }
        response = self.client.post(self.bulk_edit_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("You cannot revoke your own superuser status in a bulk edit operation.", response.content.decode('utf-8'))

        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_superuser)
