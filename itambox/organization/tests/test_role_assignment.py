"""Tests for roles/memberships findability + bulk assignment (PLAN_roles_bulk_assignment)."""
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from organization.models import Tenant, TenantMembership, TenantRole
from organization.filters import TenantRoleFilterSet, TenantMembershipFilterSet

User = get_user_model()


def _make_role(tenant, name, perms=None):
    return TenantRole.objects.create(tenant=tenant, name=name, permissions=perms or [])


def _make_membership(user, tenant, role):
    return TenantMembership.objects.create(user=user, tenant=tenant, role=role)


class TenantRoleFilterSetTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.role1 = _make_role(self.tenant_a, "Admin", ["assets.view_asset"])
        self.role2 = _make_role(self.tenant_a, "Viewer", ["assets.view_asset"])
        self.role3 = _make_role(self.tenant_b, "Manager", [])
        self.user = User.objects.create_user(username="u1", email="u1@example.com", password="pw")

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_search_by_name(self):
        qs = TenantRole.objects.all()
        f = TenantRoleFilterSet(data={'q': 'Admin'}, queryset=qs)
        self.assertIn(self.role1, f.qs)
        self.assertNotIn(self.role2, f.qs)

    def test_search_by_description(self):
        self.role2.description = "Read-only access"
        self.role2.save()
        qs = TenantRole.objects.all()
        f = TenantRoleFilterSet(data={'q': 'read-only'}, queryset=qs)
        self.assertIn(self.role2, f.qs)
        self.assertNotIn(self.role1, f.qs)

    def test_filter_by_tenant(self):
        qs = TenantRole.objects.all()
        f = TenantRoleFilterSet(data={'tenant': self.tenant_b.pk}, queryset=qs)
        self.assertIn(self.role3, f.qs)
        self.assertNotIn(self.role1, f.qs)

    def test_member_count_annotation_correct(self):
        from django.db.models import Count
        _make_membership(self.user, self.tenant_a, self.role1)
        qs = TenantRole.objects.annotate(member_count=Count('memberships', distinct=True))
        role1 = qs.get(pk=self.role1.pk)
        role2 = qs.get(pk=self.role2.pk)
        self.assertEqual(role1.member_count, 1)
        self.assertEqual(role2.member_count, 0)  # zero for unassigned

    def test_empty_search_returns_all(self):
        qs = TenantRole.objects.all()
        f = TenantRoleFilterSet(data={'q': ''}, queryset=qs)
        self.assertEqual(f.qs.count(), qs.count())


class TenantMembershipFilterSetTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="ta")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tb")
        self.role_a = _make_role(self.tenant_a, "Admin")
        self.role_b = _make_role(self.tenant_b, "User")
        self.user1 = User.objects.create_user(username="alice", email="alice@example.com", password="pw")
        self.user2 = User.objects.create_user(username="bob", email="bob@example.com", password="pw")
        self.m1 = _make_membership(self.user1, self.tenant_a, self.role_a)
        self.m2 = _make_membership(self.user2, self.tenant_b, self.role_b)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_filter_by_tenant(self):
        qs = TenantMembership.objects.all()
        f = TenantMembershipFilterSet(data={'tenant': self.tenant_a.pk}, queryset=qs)
        self.assertIn(self.m1, f.qs)
        self.assertNotIn(self.m2, f.qs)

    def test_filter_by_role(self):
        qs = TenantMembership.objects.all()
        f = TenantMembershipFilterSet(data={'role': self.role_b.pk}, queryset=qs)
        self.assertIn(self.m2, f.qs)
        self.assertNotIn(self.m1, f.qs)

    def test_filter_by_user(self):
        qs = TenantMembership.objects.all()
        f = TenantMembershipFilterSet(data={'user': self.user1.pk}, queryset=qs)
        self.assertIn(self.m1, f.qs)
        self.assertNotIn(self.m2, f.qs)

    def test_search_by_username(self):
        qs = TenantMembership.objects.all()
        f = TenantMembershipFilterSet(data={'q': 'alice'}, queryset=qs)
        self.assertIn(self.m1, f.qs)
        self.assertNotIn(self.m2, f.qs)

    def test_search_by_role_name(self):
        qs = TenantMembership.objects.all()
        f = TenantMembershipFilterSet(data={'q': 'User'}, queryset=qs)
        self.assertIn(self.m2, f.qs)
        self.assertNotIn(self.m1, f.qs)


class TenantRoleBulkDeleteViewTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Corp", slug="corp")
        self.superuser = User.objects.create_superuser(username="su", email="su@x.com", password="pw")
        self.role_free = _make_role(self.tenant, "Deletable")
        self.role_protected = _make_role(self.tenant, "Protected")
        self.member_user = User.objects.create_user(username="mu", email="mu@x.com", password="pw")
        _make_membership(self.member_user, self.tenant, self.role_protected)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_unassigned_role_deleted(self):
        self._login()
        url = reverse('organization:tenantrole_bulk_delete')
        # First POST shows confirmation form
        resp = self.client.post(url, {'pk': [self.role_free.pk], 'return_url': reverse('organization:tenantrole_list')})
        self.assertIn(resp.status_code, (200, 302))
        # Confirm deletion
        resp = self.client.post(url, {
            'pk': [self.role_free.pk],
            '_confirm': '1',
            'return_url': reverse('organization:tenantrole_list'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(TenantRole.objects.filter(pk=self.role_free.pk).exists())

    def test_protected_role_gives_readable_error_not_500(self):
        self._login()
        url = reverse('organization:tenantrole_bulk_delete')
        resp = self.client.post(url, {
            'pk': [self.role_protected.pk],
            '_confirm': '1',
            'return_url': reverse('organization:tenantrole_list'),
        })
        # Must redirect with an error message, not a 500
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TenantRole.objects.filter(pk=self.role_protected.pk).exists())
        msgs = [str(m) for m in self.client.get(resp.url).context.get('messages', [])]
        self.assertTrue(any('protected' in m.lower() or 'member' in m.lower() for m in msgs))


class TenantMembershipEditViewTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Corp", slug="corp2")
        self.superuser = User.objects.create_superuser(username="su2", email="su2@x.com", password="pw")
        self.member_user = User.objects.create_user(username="member", email="m@x.com", password="pw")
        self.role_a = _make_role(self.tenant, "RoleA")
        self.role_b = _make_role(self.tenant, "RoleB")
        self.membership = _make_membership(self.member_user, self.tenant, self.role_a)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_role_change_persists(self):
        self._login()
        url = reverse('organization:tenantmembership_update', kwargs={'pk': self.membership.pk})
        resp = self.client.post(url, {
            'user': self.member_user.pk,
            'tenant': self.tenant.pk,
            'role': self.role_b.pk,
        })
        self.assertIn(resp.status_code, (200, 302))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role_id, self.role_b.pk)

    def test_cross_tenant_role_rejected(self):
        other_tenant = Tenant.objects.create(name="Other", slug="other")
        other_role = _make_role(other_tenant, "OtherRole")
        self._login()
        url = reverse('organization:tenantmembership_update', kwargs={'pk': self.membership.pk})
        resp = self.client.post(url, {
            'user': self.member_user.pk,
            'tenant': self.tenant.pk,
            'role': other_role.pk,
        })
        # Form should be invalid — clean() rejects cross-tenant role
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role_id, self.role_a.pk)  # unchanged


class TenantMembershipBulkEditViewTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant_a = Tenant.objects.create(name="Alpha", slug="alpha")
        self.tenant_b = Tenant.objects.create(name="Beta", slug="beta")
        self.superuser = User.objects.create_superuser(username="su3", email="su3@x.com", password="pw")
        self.role_a1 = _make_role(self.tenant_a, "A-Admin")
        self.role_a2 = _make_role(self.tenant_a, "A-Viewer")
        self.role_b1 = _make_role(self.tenant_b, "B-Admin")
        self.user1 = User.objects.create_user(username="u_a", email="ua@x.com", password="pw")
        self.user2 = User.objects.create_user(username="u_b", email="ub@x.com", password="pw")
        self.mem1 = _make_membership(self.user1, self.tenant_a, self.role_a1)
        self.mem2 = _make_membership(self.user2, self.tenant_b, self.role_b1)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

    def _url(self):
        return reverse('organization:tenantmembership_bulk_edit')

    def test_happy_path_reassigns_role(self):
        self._login()
        resp = self.client.post(self._url(), {
            'pk': [self.mem1.pk],
            '_apply': '1',
            'role': self.role_a2.pk,
            'return_url': reverse('organization:tenantmembership_list'),
        })
        self.assertEqual(resp.status_code, 302)
        self.mem1.refresh_from_db()
        self.assertEqual(self.mem1.role_id, self.role_a2.pk)

    def test_cross_tenant_role_rejected(self):
        self._login()
        resp = self.client.post(self._url(), {
            'pk': [self.mem1.pk],
            '_apply': '1',
            'role': self.role_b1.pk,  # belongs to tenant_b, not tenant_a
            'return_url': reverse('organization:tenantmembership_list'),
        })
        self.assertEqual(resp.status_code, 302)
        self.mem1.refresh_from_db()
        self.assertEqual(self.mem1.role_id, self.role_a1.pk)  # unchanged

    def test_multi_tenant_batch_rejected(self):
        """Memberships from two different tenants in one POST must be rejected."""
        self._login()
        resp = self.client.post(self._url(), {
            'pk': [self.mem1.pk, self.mem2.pk],
            '_apply': '1',
            'role': self.role_a2.pk,
            'return_url': reverse('organization:tenantmembership_list'),
        })
        self.assertEqual(resp.status_code, 302)
        # mem2 belongs to tenant_b which superuser can also admin, but mixed tenants rejected
        self.mem1.refresh_from_db()
        self.mem2.refresh_from_db()
        # At least one should be unchanged (multi-tenant check fires before any save)
        self.assertEqual(self.mem1.role_id, self.role_a1.pk)

    def test_pk_smuggling_blocked(self):
        """Non-admin user cannot reassign memberships of a tenant they don't administer."""
        limited_user = User.objects.create_user(username="limited", email="lim@x.com", password="pw")
        # limited_user is a member of tenant_a with role_a1 (no change_tenantmembership perm)
        limited_role = _make_role(self.tenant_a, "LimitedRole", ["assets.view_asset"])
        limited_mem = _make_membership(limited_user, self.tenant_a, limited_role)

        self.client.force_login(limited_user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # limited_user tries to reassign mem1 (which they don't own / can't admin)
        resp = self.client.post(self._url(), {
            'pk': [self.mem1.pk],
            '_apply': '1',
            'role': self.role_a2.pk,
            'return_url': reverse('organization:tenantmembership_list'),
        })
        # Should be blocked by permission checks (403 or redirect with error)
        self.mem1.refresh_from_db()
        self.assertEqual(self.mem1.role_id, self.role_a1.pk)  # unchanged

    def test_permission_denial_redirects(self):
        """A user with no tenant admin perms gets a non-500 response."""
        ordinary_user = User.objects.create_user(username="ordinary", email="ord@x.com", password="pw")
        self.client.force_login(ordinary_user)
        resp = self.client.post(self._url(), {
            'pk': [self.mem1.pk],
            '_apply': '1',
            'role': self.role_a2.pk,
        })
        self.assertIn(resp.status_code, (302, 403))


class TenantRoleAssignUsersViewTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Assign Corp", slug="assign-corp")
        self.superuser = User.objects.create_superuser(username="su_assign", email="sua@x.com", password="pw")
        self.role = _make_role(self.tenant, "Target Role")
        self.user_new = User.objects.create_user(username="new_user", email="new@x.com", password="pw")
        self.user_other_role = User.objects.create_user(username="other_role", email="or@x.com", password="pw")
        self.user_same_role = User.objects.create_user(username="same_role", email="sr@x.com", password="pw")
        self.other_role = _make_role(self.tenant, "Other Role")
        _make_membership(self.user_other_role, self.tenant, self.other_role)
        _make_membership(self.user_same_role, self.tenant, self.role)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def _url(self):
        return reverse('organization:tenantrole_assign_users', kwargs={'pk': self.role.pk})

    def test_creates_new_membership(self):
        self._login()
        resp = self.client.post(self._url(), {'users': [self.user_new.pk]})
        self.assertIn(resp.status_code, (200, 302))
        self.assertTrue(TenantMembership.objects.filter(user=self.user_new, tenant=self.tenant, role=self.role).exists())

    def test_updates_existing_membership_with_different_role(self):
        self._login()
        resp = self.client.post(self._url(), {'users': [self.user_other_role.pk]})
        self.assertIn(resp.status_code, (200, 302))
        self.user_other_role.memberships.get(tenant=self.tenant).refresh_from_db()
        self.assertEqual(self.user_other_role.memberships.get(tenant=self.tenant).role_id, self.role.pk)

    def test_noop_for_user_already_with_this_role(self):
        self._login()
        # Should not raise, should not create a second membership
        resp = self.client.post(self._url(), {'users': [self.user_same_role.pk]})
        self.assertIn(resp.status_code, (200, 302))
        self.assertEqual(TenantMembership.objects.filter(user=self.user_same_role, tenant=self.tenant).count(), 1)

    def test_counts_in_success_message(self):
        self._login()
        resp = self.client.post(self._url(), {
            'users': [self.user_new.pk, self.user_other_role.pk, self.user_same_role.pk],
        }, follow=True)
        content = str(resp.content)
        self.assertIn('1 added', content)
        self.assertIn('1 updated', content)
        self.assertIn('1 unchanged', content)

    def test_permission_denial_returns_403(self):
        """A view-only user (no add/change_tenantmembership) gets 403."""
        view_only_role = _make_role(self.tenant, "ViewOnly", ["assets.view_asset"])
        view_user = User.objects.create_user(username="vo", email="vo@x.com", password="pw")
        _make_membership(view_user, self.tenant, view_only_role)

        self.client.force_login(view_user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)
