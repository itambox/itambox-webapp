"""Regression tests for D1-1 (release-blockers-2026-07-09).

``Membership`` (organization/models.py) intentionally uses Django's plain,
unscoped default manager: tenant resolution itself is derived from
Membership, so it cannot depend on tenant scoping (see the comment in
``core/managers.py``'s ``TenantScopingQuerySet.filter_by_tenant()``). That
means ``TenantScopingViewMixin.get_queryset()`` (applied to every
``ObjectListView``/``ObjectDetailView`` via ``filter_by_tenant()``) is a
silent no-op for Membership.

Before this fix, ``MembershipListView``/``MembershipDetailView`` applied no
manual restriction on top of that — unlike ``MembershipBulkEditView``/
``MembershipBulkDeleteView`` in the same file, which correctly restrict to
containers (tenants/providers) the requesting user actually administers. An
ordinary tenant member holding only ``organization.view_membership`` (e.g. a
Read-Only role — every default seeded role includes it) could list every
tenant's and every provider's Membership rows, and a cross-tenant detail GET
returned an existence-revealing 403 instead of a 404.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organization.models import Membership, Role, Tenant

User = get_user_model()


class MembershipListDetailCrossTenantTestCase(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='MCTS Tenant A', slug='mcts-tenant-a')
        self.tenant_b = Tenant.objects.create(name='MCTS Tenant B', slug='mcts-tenant-b')

        # Tenant-A user holding only an ordinary Read-Only-style role — no
        # special privilege is needed to reproduce the leak.
        self.user_a = User.objects.create_user(username='mcts_user_a', password='password123')
        self.role_a = Role.objects.create(
            tenant=self.tenant_a, name='MCTS Read-Only',
            permissions=['organization.view_membership'],
        )
        self.membership_a = Membership.objects.create(user=self.user_a, tenant=self.tenant_a)
        self.membership_a.roles.add(self.role_a)

        # Tenant-B's own membership row — must never be visible to Tenant A.
        self.user_b = User.objects.create_user(username='mcts_user_b', password='password123')
        self.membership_b = Membership.objects.create(user=self.user_b, tenant=self.tenant_b)

    def _login_tenant_a(self):
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

    def test_list_excludes_other_tenant_membership(self):
        """Tenant A's list view returns only its own memberships, not tenant B's."""
        self._login_tenant_a()
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        returned_pks = {m.pk for m in response.context['object_list']}
        self.assertIn(self.membership_a.pk, returned_pks)
        self.assertNotIn(self.membership_b.pk, returned_pks)

    def test_detail_own_membership_ok(self):
        """Sanity check: the fix must not also hide the user's own membership."""
        self._login_tenant_a()
        response = self.client.get(
            reverse('organization:membership_detail', kwargs={'pk': self.membership_a.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_detail_other_tenant_membership_404(self):
        """Tenant A GET on tenant B's membership detail is 404 (no enumeration)."""
        self._login_tenant_a()
        response = self.client.get(
            reverse('organization:membership_detail', kwargs={'pk': self.membership_b.pk})
        )
        self.assertEqual(response.status_code, 404)


class MembershipExportCrossTenantTestCase(TestCase):
    """The generic ``ObjectExportView`` (itambox/views/features.py) is a
    sibling data path to MembershipListView/-DetailView: it builds its
    queryset from ``model.objects.all()`` too, so it is equally exposed to
    Membership's unscoped default manager. Gating only on the ambient
    ``organization.view_membership`` permission (no ``obj=``) would let any
    Read-Only tenant member export every tenant's/provider's Membership rows
    via CSV/YAML, defeating the exact guarantee the list/detail fix
    establishes.
    """

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='MECT Tenant A', slug='mect-tenant-a')
        self.tenant_b = Tenant.objects.create(name='MECT Tenant B', slug='mect-tenant-b')

        self.user_a = User.objects.create_user(username='mect_user_a', password='password123')
        self.role_a = Role.objects.create(
            tenant=self.tenant_a, name='MECT Read-Only',
            permissions=['organization.view_membership'],
        )
        self.membership_a = Membership.objects.create(user=self.user_a, tenant=self.tenant_a)
        self.membership_a.roles.add(self.role_a)

        self.user_b = User.objects.create_user(username='mect_user_b', password='password123')
        self.membership_b = Membership.objects.create(user=self.user_b, tenant=self.tenant_b)

    def _login_tenant_a(self):
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

    def test_export_excludes_other_tenant_membership(self):
        self._login_tenant_a()
        url = reverse('object_export', kwargs={
            'app_label': 'organization', 'model_name': 'membership', 'template_id': 0,
        })
        response = self.client.get(url + '?format=csv')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(self.user_a.username, content)
        self.assertNotIn(self.user_b.username, content)
