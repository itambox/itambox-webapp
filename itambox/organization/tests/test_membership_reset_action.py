"""Regression tests for FIX #5 (RBAC review §3-E, defect #5).

The ``send_invite`` checkbox on technician onboarding was a no-op (collected but never
read), so onboarded staff got ``set_unusable_password()`` with no way to log in. The fix:

  * remove the misleading ``send_invite`` field from ``TechnicianQuickForm``; and
  * add a manual "send password-reset / set-password link" action on the membership
    detail page, guarded so only a manager of the membership's tenant (or a superuser)
    can trigger it.

These tests cover:
  (a) ``TechnicianQuickForm`` no longer declares ``send_invite``;
  (b) the send-reset action, when POSTed by an authorized manager, sends exactly one email;
  (c) an unauthorized user is denied and no email is sent.

Fixture note (RBAC stage-2 structural collapse): the old ``Provider`` model + Membership
``roles`` M2M are gone. A managing (MSP) organization is now a ``Tenant(is_provider=True)``;
grants are per-row ``RoleAssignment``s created via the ``grant()`` test helper. The view's
own permission check (``organization.change_membership`` on the membership's tenant) is
unchanged — only the fixture setup below is migrated.
"""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin
from organization.forms.provider_form import TechnicianQuickForm
from organization.models import Tenant, Membership, Role

User = get_user_model()


class TechnicianQuickFormSendInviteRemovedTests(TestCase):
    def test_form_has_no_send_invite_field(self):
        form = TechnicianQuickForm()
        self.assertNotIn('send_invite', form.fields)


class MembershipSendResetActionTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        # A managing (MSP) tenant whose staff memberships we manage.
        self.provider = Tenant.objects.create(
            name="Acme MSP", slug="acme-msp", is_provider=True,
        )

        # A role, owned by the managing tenant, that lets its holder change
        # memberships there.
        self.manager_role = Role.objects.create(
            tenant=self.provider,
            name="Staff Manager",
            permissions=["organization.change_membership"],
        )

        # The acting manager: a non-superuser staff member holding manager_role via an
        # own-reach RoleAssignment at the managing tenant.
        self.manager = User.objects.create_user(
            username="manager", email="manager@example.com",
            password="pw", is_active=True,
        )
        self.grant(self.manager, self.provider, self.manager_role)
        self.manager_membership = Membership.objects.get(
            user=self.manager, tenant=self.provider,
        )

        # The onboarded technician whose credential we want to (re)issue. Mirrors the
        # onboarding flow: usable email, unusable password.
        self.tech = User.objects.create_user(
            username="tech", email="tech@example.com", is_active=True,
        )
        self.tech.set_unusable_password()
        self.tech.save(update_fields=["password"])
        self.tech_membership = Membership.objects.create(
            user=self.tech, tenant=self.provider, is_active=True,
        )

        # An outsider with no permission on the managing tenant.
        self.outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com",
            password="pw", is_active=True,
        )

        self.url = reverse(
            'organization:membership_send_reset', kwargs={'pk': self.tech_membership.pk},
        )
        self.detail_url = reverse(
            'organization:membership_detail', kwargs={'pk': self.tech_membership.pk},
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_authorized_manager_sends_exactly_one_email(self):
        self.client.force_login(self.manager)
        resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, self.detail_url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.tech.email, mail.outbox[0].to)

        messages = list(resp.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level_tag, 'success')

    def test_superuser_may_send(self):
        superuser = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw",
        )
        self.client.force_login(superuser)
        resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_unauthorized_user_is_denied_and_sends_no_email(self):
        self.client.force_login(self.outsider)
        resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, self.detail_url)
        self.assertEqual(len(mail.outbox), 0)

        messages = list(resp.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level_tag, 'error')

    def test_get_is_not_allowed(self):
        self.client.force_login(self.manager)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
