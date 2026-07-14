"""WS2 regression suite — authorize the tenant context before rendering/cleaning.

``MembershipCreateView`` must AUTHORIZE the tenant a membership will belong to
before it builds or renders the form, so an explicit ``?tenant=<pk>`` the
requester may not use 404s (never confirming its existence or leaking its roles /
managed tenants), a tampered hidden tenant field cannot act as a membership
oracle, and only a superuser gets the context-free global picker
(``RBAC_STAGE3_POST_REVIEW_FIX_PLAN.md`` §2).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import grant
from organization.models import Membership, Role, Tenant
from organization.forms.membership_form import MembershipForm

from ._membership_form_helpers import membership_post_data

User = get_user_model()

ADMIN_PERMS = [
    'organization.add_membership',
    'organization.change_membership',
    'organization.view_membership',
]


class MembershipCreateAuthzTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant_a = Tenant.objects.create(name='Authz A', slug='authz-a')
        self.tenant_b = Tenant.objects.create(name='SecretCorp B', slug='authz-b')

        self.admin_a = User.objects.create_user(username='authz_admin_a', password='pw')
        self.role_a = Role.objects.create(
            tenant=self.tenant_a, name='A Admin', permissions=ADMIN_PERMS,
        )
        grant(self.admin_a, self.tenant_a, self.role_a)

        # A user already a member of tenant B — the membership-oracle target.
        self.victim = User.objects.create_user(
            username='authz_victim', email='victim@authz.test', password='pw',
        )
        Membership.objects.create(user=self.victim, tenant=self.tenant_b, is_active=True)

        self.superuser = User.objects.create_superuser(
            username='authz_root', email='authz_root@x.com', password='pw',
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self, user, active_tenant=None):
        self.client.force_login(user)
        session = self.client.session
        if active_tenant is not None:
            session['active_tenant_id'] = active_tenant.pk
        session.save()
        if active_tenant is not None and not user.is_superuser:
            membership = Membership.objects.filter(user=user, tenant=active_tenant).first()
            set_current_tenant(active_tenant)
            set_current_membership(membership)

    # --- explicit deep link to an unauthorized tenant --------------------------
    def test_get_explicit_unauthorized_tenant_is_404_and_leaks_nothing(self):
        self._login(self.admin_a, active_tenant=self.tenant_a)
        url = reverse('organization:membership_create') + f'?tenant={self.tenant_b.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'SecretCorp B', response.content)

    def test_post_explicit_unauthorized_tenant_is_404_and_writes_nothing(self):
        self._login(self.admin_a, active_tenant=self.tenant_a)
        url = reverse('organization:membership_create') + f'?tenant={self.tenant_b.pk}'
        newcomer = User.objects.create_user(username='newcomer', password='pw')
        response = self.client.post(url, data=membership_post_data(
            user=newcomer.pk, tenant=self.tenant_b.pk,
        ))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Membership.objects.filter(user=newcomer).exists())

    # --- tampered hidden tenant field (no ?tenant; active tenant A) -------------
    def test_tampered_tenant_with_existing_member_is_no_oracle_and_no_write(self):
        self._login(self.admin_a, active_tenant=self.tenant_a)
        url = reverse('organization:membership_create')
        before = Membership.objects.filter(tenant=self.tenant_b).count()
        response = self.client.post(url, data=membership_post_data(
            who=MembershipForm.WHO_NEW, tenant=self.tenant_b.pk,
            new_user_email='victim@authz.test',  # already a member of B
            new_user_first_name='V', new_user_last_name='Ictim',
        ))
        # Form invalid (tenant not in the authorized queryset) — re-rendered, not a
        # redirect, and it must NOT reveal B's membership state.
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'already a member', response.content)
        self.assertEqual(Membership.objects.filter(tenant=self.tenant_b).count(), before)

    def test_tampered_tenant_with_new_email_writes_nothing_to_b(self):
        self._login(self.admin_a, active_tenant=self.tenant_a)
        url = reverse('organization:membership_create')
        response = self.client.post(url, data=membership_post_data(
            who=MembershipForm.WHO_NEW, tenant=self.tenant_b.pk,
            new_user_email='brand_new@authz.test',
            new_user_first_name='Brand', new_user_last_name='New',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Membership.objects.filter(tenant=self.tenant_b).filter(
            user__email='brand_new@authz.test').exists())
        self.assertFalse(User.objects.filter(email='brand_new@authz.test').exists())

    # --- malformed / nonexistent ids -------------------------------------------
    def test_nonnumeric_explicit_tenant_is_404_not_500(self):
        self._login(self.admin_a, active_tenant=self.tenant_a)
        url = reverse('organization:membership_create') + '?tenant=not-a-number'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_explicit_tenant_is_404(self):
        self._login(self.admin_a, active_tenant=self.tenant_a)
        url = reverse('organization:membership_create') + '?tenant=99999999'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # --- authorized happy paths -------------------------------------------------
    def test_authorized_admin_get_and_post_succeed(self):
        self._login(self.admin_a, active_tenant=self.tenant_a)
        url = reverse('organization:membership_create') + f'?tenant={self.tenant_a.pk}'
        self.assertEqual(self.client.get(url).status_code, 200)

        newcomer = User.objects.create_user(username='a_newcomer', password='pw')
        response = self.client.post(url, data=membership_post_data(
            user=newcomer.pk, tenant=self.tenant_a.pk,
        ))
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None)
                         and response.context['form'].errors.as_json())
        self.assertTrue(Membership.objects.filter(user=newcomer, tenant=self.tenant_a).exists())

    def test_superuser_context_free_create_renders_global_picker(self):
        self._login(self.superuser)
        response = self.client.get(reverse('organization:membership_create'))
        self.assertEqual(response.status_code, 200)
        # Global picker: the tenant field is a real (non-hidden) select of tenants.
        form = response.context['form']
        self.assertIsNone(getattr(form, '_membership_tenant', 'x'))

    def test_member_without_add_membership_fails_closed(self):
        # A member of A holding only view_membership (no add_membership): their
        # active tenant resolves to A, but they may not add members there → fail
        # closed (403), never the global picker, never a leak.
        viewer = User.objects.create_user(username='authz_viewer', password='pw')
        viewer_role = Role.objects.create(
            tenant=self.tenant_a, name='A Viewer',
            permissions=['organization.view_membership'],
        )
        grant(viewer, self.tenant_a, viewer_role)
        self._login(viewer, active_tenant=self.tenant_a)
        response = self.client.get(reverse('organization:membership_create'))
        self.assertIn(response.status_code, (403, 302))


class MembershipFormOracleDefenseTests(TestCase):
    """Form-level defense-in-depth: a directly-built form must not reveal another
    tenant's membership state to an actor who may not manage it."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name='Oracle Corp', slug='oracle-corp')
        self.member = User.objects.create_user(
            username='oracle_member', email='member@oracle.test', password='pw',
        )
        Membership.objects.create(user=self.member, tenant=self.tenant, is_active=True)
        self.outsider = User.objects.create_user(
            username='oracle_outsider', email='out@oracle.test', password='pw',
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_unauthorized_actor_gets_a_non_revealing_error(self):
        form = MembershipForm(
            data=membership_post_data(
                who=MembershipForm.WHO_NEW, tenant=self.tenant.pk,
                new_user_email='member@oracle.test',
                new_user_first_name='M', new_user_last_name='Ember',
            ),
            tenant=self.tenant, user=self.outsider,
        )
        self.assertFalse(form.is_valid())
        errors = ' '.join(form.errors.get('new_user_email', []))
        self.assertNotIn('already a member', errors)
        self.assertIn('cannot be added', errors)
