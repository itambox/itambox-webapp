"""WS3 regression suite — shared-in role detail authorization + scoped counts.

A role a managing (``is_provider``) tenant shares down (``shared_with_managed``)
must be READABLE by a managed-tenant admin holding local ``view_role`` — the
generic object check denies them because ``view_role`` obj=role resolves against
the provider-owned tenant they are not a member of. The read must stay read-only,
must not expose an UNSHARED provider role, and its member count must be scoped to
the tenant being viewed (never leak sibling customers' / provider-internal
counts). See ``RBAC_STAGE3_POST_REVIEW_FIX_PLAN.md`` §3.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleAssignment, Tenant

User = get_user_model()


class SharedRoleDetailAuthzTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Tenant.objects.create(name='RDA Provider', slug='rda-p', is_provider=True)
        self.cust = Tenant.objects.create(name='RDA Customer', slug='rda-c', managed_by=self.provider)
        self.sibling = Tenant.objects.create(name='RDA Sibling', slug='rda-d', managed_by=self.provider)

        self.shared_role = Role.objects.create(
            tenant=self.provider, name='Shared Tech',
            shared_with_managed=True, permissions=['assets.view_asset'],
        )
        self.unshared_role = Role.objects.create(
            tenant=self.provider, name='Provider Internal',
            shared_with_managed=False, permissions=[],
        )

        # Customer admin: member of C holding local view_role only.
        self.c_admin = User.objects.create_user(username='rda_c_admin', password='pw')
        self.c_admin_role = Role.objects.create(
            tenant=self.cust, name='C Admin', permissions=['organization.view_role'],
        )
        grant(self.c_admin, self.cust, self.c_admin_role)

        # Provider owner-admin: member of P holding view/change role.
        self.p_admin = User.objects.create_user(username='rda_p_admin', password='pw')
        self.p_admin_role = Role.objects.create(
            tenant=self.provider, name='P Admin',
            permissions=['organization.view_role', 'organization.change_role',
                         'organization.add_membership', 'organization.change_membership'],
        )
        grant(self.p_admin, self.provider, self.p_admin_role)

        self.superuser = User.objects.create_superuser(
            username='rda_root', email='rda_root@x.com', password='pw',
        )

        # Assign the shared role: 2 members in C, 1 in the sibling D, 1 in P.
        for i in range(2):
            u = User.objects.create_user(username=f'rda_c_member_{i}', password='pw')
            grant(u, self.cust, self.shared_role)
        grant(User.objects.create_user(username='rda_d_member', password='pw'),
              self.sibling, self.shared_role)
        grant(User.objects.create_user(username='rda_p_staff', password='pw'),
              self.provider, self.shared_role)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self, user, active_tenant):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = active_tenant.pk
        session.save()
        membership = Membership.objects.filter(user=user, tenant=active_tenant).first()
        set_current_tenant(active_tenant)
        set_current_membership(membership)

    def _detail_url(self, role):
        return reverse('organization:role_detail', kwargs={'pk': role.pk})

    def test_customer_admin_can_read_shared_role_read_only(self):
        self._login(self.c_admin, self.cust)
        response = self.client.get(self._detail_url(self.shared_role))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['shared_in_role'])
        self.assertFalse(response.context['role_editable'])
        self.assertFalse(response.context['can_change'])
        self.assertFalse(response.context['can_delete'])
        self.assertIsNone(response.context['action_urls']['edit'])
        self.assertNotIn(b'Assign Users', response.content)

    def test_customer_admin_cannot_open_an_unshared_provider_role(self):
        self._login(self.c_admin, self.cust)
        response = self.client.get(self._detail_url(self.unshared_role))
        self.assertEqual(response.status_code, 404)

    def test_customer_admin_cannot_edit_or_delete_the_shared_role(self):
        self._login(self.c_admin, self.cust)
        edit = self.client.get(reverse('organization:role_update', kwargs={'pk': self.shared_role.pk}))
        self.assertIn(edit.status_code, (403, 404))
        delete = self.client.post(reverse('organization:role_delete', kwargs={'pk': self.shared_role.pk}))
        self.assertIn(delete.status_code, (403, 404))
        # _base_manager: the shared role is provider-owned, invisible to the
        # tenant-scoped default manager under the customer's active context.
        self.assertIsNone(Role._base_manager.get(pk=self.shared_role.pk).deleted_at)

    def test_shared_in_member_count_is_scoped_to_the_active_tenant(self):
        self._login(self.c_admin, self.cust)
        response = self.client.get(self._detail_url(self.shared_role))
        # Only the 2 memberships in C — provider staff and the sibling customer's
        # assignment are excluded.
        self.assertEqual(response.context['member_count'], 2)
        # And that equals an independent scoped count of the same set.
        scoped = (
            RoleAssignment.objects.filter(role=self.shared_role, membership__tenant=self.cust)
            .values('membership_id').distinct().count()
        )
        self.assertEqual(scoped, 2)

    def test_provider_name_is_plain_text_without_view_tenant(self):
        self._login(self.c_admin, self.cust)
        response = self.client.get(self._detail_url(self.shared_role))
        self.assertIsNone(response.context['provider_tenant_url'])
        self.assertEqual(response.context['provider_tenant_name'], 'RDA Provider')

    def test_provider_owner_controls_remain_valid(self):
        self._login(self.p_admin, self.provider)
        response = self.client.get(self._detail_url(self.shared_role))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['shared_in_role'])
        self.assertTrue(response.context['role_editable'])
        # Owner context: count scoped to the provider tenant (its 1 staff holder).
        self.assertEqual(response.context['member_count'], 1)
        self.assertIn(b'Assign Users', response.content)

    def test_superuser_global_sees_the_unscoped_total(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self._detail_url(self.shared_role))
        self.assertEqual(response.status_code, 200)
        # Global annotation: every membership holding the role (2 C + 1 D + 1 P).
        self.assertEqual(response.context['member_count'], 4)
