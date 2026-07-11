"""Stage-3 post-review small follow-ups.

  * ``tenant_access_report`` flags globally-inactive / no-login accounts so the
    outside-access panel does not claim they "can access".
  * ``RoleAssignment.covers_tenant`` returns the same answer whether or not
    ``assigned_tenants`` is prefetched (the report prefetches it via _base_manager
    to avoid one query per explicit assignment).
  * The membership detail's password-reset button is gated on ``can_change``.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import grant
from organization.access import tenant_access_report
from organization.models import Membership, Role, RoleAssignment, Tenant

User = get_user_model()


class OutsideAccessInactiveFlagTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Tenant.objects.create(name='FU P', slug='fu-p', is_provider=True)
        self.cust = Tenant.objects.create(name='FU C', slug='fu-c', managed_by=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider, name='Tech', permissions=['assets.view_asset'],
            shared_with_managed=True,
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _staff(self, username, **user_kwargs):
        user = User.objects.create_user(username=username, password='pw', **user_kwargs)
        grant(user, self.provider, self.role, reach=RoleAssignment.REACH_MANAGED,
              managed_scope=RoleAssignment.SCOPE_EXPLICIT, assigned_tenants=[self.cust])
        return user

    def test_disabled_and_no_login_accounts_are_flagged_inactive(self):
        self._staff('fu_active')
        self._staff('fu_disabled', is_active=False)
        self._staff('fu_nologin', can_login=False)
        report = {e['user'].username: e for e in tenant_access_report(self.cust, external_only=True)}
        self.assertFalse(report['fu_active']['inactive'])
        self.assertTrue(report['fu_disabled']['inactive'])
        self.assertTrue(report['fu_nologin']['inactive'])

    def test_covers_tenant_is_consistent_prefetched_or_not(self):
        staff = self._staff('fu_cover')
        assignment = RoleAssignment.objects.get(
            membership__user=staff, reach=RoleAssignment.REACH_MANAGED,
        )
        # Unprefetched (queries via _base_manager) and prefetched (reads the cache)
        # must agree — both cover cust_a, neither covers a stranger tenant.
        stranger = Tenant.objects.create(name='FU S', slug='fu-s', managed_by=self.provider)
        self.assertTrue(assignment.covers_tenant(self.cust))
        self.assertFalse(assignment.covers_tenant(stranger))

        prefetched = RoleAssignment.objects.prefetch_related('assigned_tenants').get(pk=assignment.pk)
        self.assertTrue(prefetched.covers_tenant(self.cust))
        self.assertFalse(prefetched.covers_tenant(stranger))


class MembershipResetButtonGateTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name='FU RB', slug='fu-rb')
        self.member = User.objects.create_user(
            username='fu_member', email='fu_member@x.com', password='pw',
        )
        self.membership = Membership.objects.create(user=self.member, tenant=self.tenant)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()
        membership = Membership.objects.filter(user=user, tenant=self.tenant).first()
        set_current_tenant(self.tenant)
        set_current_membership(membership)

    def _detail(self):
        return self.client.get(
            reverse('organization:membership_detail', kwargs={'pk': self.membership.pk})
        )

    def test_button_hidden_for_viewer_who_cannot_change(self):
        viewer = User.objects.create_user(username='fu_viewer', password='pw')
        grant(viewer, self.tenant, Role.objects.create(
            tenant=self.tenant, name='Viewer', permissions=['organization.view_membership']))
        self._login(viewer)
        response = self._detail()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'send-password-setup', response.content)

    def test_button_shown_for_viewer_who_can_change(self):
        admin = User.objects.create_user(username='fu_admin', password='pw')
        grant(admin, self.tenant, Role.objects.create(
            tenant=self.tenant, name='Admin',
            permissions=['organization.view_membership', 'organization.change_membership']))
        self._login(admin)
        response = self._detail()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'send-password-setup', response.content)
