"""Integration tests for the TOTP MFA gate (local-password logins only).

Covers the policy helpers (``core.mfa``), the enforcement middleware
(``core.otp_middleware.OTPEnforcementMiddleware``) and the enroll/verify gate
view (``core.views.mfa.MFASetupView``).

MFA is enforced only for *local password* sessions of superusers / owner|admin
role holders. SSO/LDAP/SAML/OIDC sessions set a different ``_auth_user_backend``
on the session and are exempt; token-API requests have no session backend.

This suite is order-dependent: fixtures use the ``-mfa`` suffix and unique names
so they never collide with the rest of the security suite.
"""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from organization.models import Tenant, TenantRole, TenantMembership
from core.mfa import user_requires_mfa, PASSWORD_BACKEND

User = get_user_model()

# A non-password backend path (OIDC) used to simulate an SSO session, which the
# enforcement middleware must treat as exempt.
SSO_BACKEND = 'core.auth.oidc.TenantOIDCBackend'


def current_totp(device):
    """Compute the current valid TOTP code for ``device`` as a zero-padded str.

    Mirrors what an authenticator app would emit. Signature confirmed against
    the installed ``django_otp.oath.totp(key, step, t0, digits, drift)``.
    """
    code = totp(
        device.bin_key,
        step=device.step,
        t0=device.t0,
        digits=device.digits,
        drift=0,
    )
    return str(code).zfill(device.digits)


class MFAPolicyHelperTests(TestCase):
    """user_requires_mfa(): superuser / owner|admin role -> True; else False."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Policy Tenant MFA', slug='policy-tenant-mfa')

        self.super_user = User.objects.create_superuser(
            username='super-mfa', email='super-mfa@example.com', password='pw-mfa',
        )

        self.admin_user = User.objects.create_user(
            username='admin-mfa', email='admin-mfa@example.com', password='pw-mfa',
        )
        admin_role = TenantRole.objects.create(tenant=self.tenant, name='admin', permissions=[])
        TenantMembership.objects.create(user=self.admin_user, tenant=self.tenant, role=admin_role)

        self.owner_user = User.objects.create_user(
            username='owner-mfa', email='owner-mfa@example.com', password='pw-mfa',
        )
        # Mixed case must still match (case-insensitive role-name check).
        owner_role = TenantRole.objects.create(tenant=self.tenant, name='Owner', permissions=[])
        TenantMembership.objects.create(user=self.owner_user, tenant=self.tenant, role=owner_role)

        self.member_user = User.objects.create_user(
            username='member-mfa', email='member-mfa@example.com', password='pw-mfa',
        )
        member_role = TenantRole.objects.create(tenant=self.tenant, name='Viewer', permissions=[])
        TenantMembership.objects.create(user=self.member_user, tenant=self.tenant, role=member_role)

    def test_superuser_requires_mfa(self):
        self.assertTrue(user_requires_mfa(self.super_user))

    def test_admin_role_requires_mfa(self):
        self.assertTrue(user_requires_mfa(self.admin_user))

    def test_owner_role_requires_mfa(self):
        self.assertTrue(user_requires_mfa(self.owner_user))

    def test_plain_member_does_not_require_mfa(self):
        self.assertFalse(user_requires_mfa(self.member_user))


@override_settings(MFA_ENFORCED=True)
class MFAEnforcementMiddlewareTests(TestCase):
    """OTPEnforcementMiddleware: redirect required+unverified password sessions.

    Enforcement is opt-in (MFA_ENFORCED, off by default in dev/test), so these
    tests turn it on explicitly — mirroring production.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Enforce Tenant MFA', slug='enforce-tenant-mfa')

        self.admin_user = User.objects.create_user(
            username='enforce-admin-mfa', email='enforce-admin-mfa@example.com', password='pw-mfa',
        )
        admin_role = TenantRole.objects.create(tenant=self.tenant, name='Admin', permissions=[])
        TenantMembership.objects.create(user=self.admin_user, tenant=self.tenant, role=admin_role)

        self.member_user = User.objects.create_user(
            username='enforce-member-mfa', email='enforce-member-mfa@example.com', password='pw-mfa',
        )
        member_role = TenantRole.objects.create(tenant=self.tenant, name='Member', permissions=[])
        TenantMembership.objects.create(user=self.member_user, tenant=self.tenant, role=member_role)

    def _login_with_backend(self, user, backend):
        """force_login then pin the session auth backend (password vs SSO)."""
        self.client.force_login(user)
        session = self.client.session
        session['_auth_user_backend'] = backend
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_password_admin_unverified_is_redirected_to_mfa(self):
        self._login_with_backend(self.admin_user, PASSWORD_BACKEND)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('mfa_setup'))

    def test_password_member_is_not_redirected_to_mfa(self):
        self._login_with_backend(self.member_user, PASSWORD_BACKEND)

        response = self.client.get(reverse('dashboard'))

        # A non-admin/owner password user is exempt: whatever the dashboard does
        # (render or its own redirect), it is never the MFA gate.
        if response.status_code == 302:
            self.assertNotEqual(response.url, reverse('mfa_setup'))

    def test_sso_admin_is_not_redirected_to_mfa(self):
        # Same admin, but an SSO (OIDC) backend on the session -> exempt.
        self._login_with_backend(self.admin_user, SSO_BACKEND)

        response = self.client.get(reverse('dashboard'))

        if response.status_code == 302:
            self.assertNotEqual(response.url, reverse('mfa_setup'))


@override_settings(MFA_ENFORCED=True)
class MFAGateViewTests(TestCase):
    """MFASetupView enroll/verify/backup-code flows on a password session."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Gate Tenant MFA', slug='gate-tenant-mfa')
        self.admin_user = User.objects.create_user(
            username='gate-admin-mfa', email='gate-admin-mfa@example.com', password='pw-mfa',
        )
        admin_role = TenantRole.objects.create(tenant=self.tenant, name='Admin', permissions=[])
        TenantMembership.objects.create(user=self.admin_user, tenant=self.tenant, role=admin_role)

    def _login_password(self, user):
        self.client.force_login(user)
        session = self.client.session
        session['_auth_user_backend'] = PASSWORD_BACKEND
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_enroll_confirms_device_issues_backup_codes_and_verifies_session(self):
        self._login_password(self.admin_user)

        # Pre-create the unconfirmed device exactly as the view's GET would, so
        # we can compute a matching current code. The view's POST get_or_creates
        # the same (user, name='default', confirmed=False) row.
        device, _ = TOTPDevice.objects.get_or_create(
            user=self.admin_user, name='default', confirmed=False,
        )

        response = self.client.post(
            reverse('mfa_setup'),
            data={'code': current_totp(device), 'next': reverse('dashboard')},
        )

        # Enroll success renders the one-time backup-code screen (HTTP 200).
        self.assertEqual(response.status_code, 200)

        device.refresh_from_db()
        self.assertTrue(device.confirmed)

        backup_device = StaticDevice.objects.get(user=self.admin_user, name='backup')
        self.assertEqual(backup_device.token_set.count(), 10)

        # Session is now OTP-verified: the dashboard is reachable, no MFA redirect.
        dash = self.client.get(reverse('dashboard'))
        if dash.status_code == 302:
            self.assertNotEqual(dash.url, reverse('mfa_setup'))

    def test_verify_already_enrolled_redirects_to_dashboard(self):
        self._login_password(self.admin_user)

        # A confirmed device with the default last_t=-1 so the current code is
        # accepted by match_token.
        device = TOTPDevice.objects.create(
            user=self.admin_user, name='default', confirmed=True,
        )

        response = self.client.post(
            reverse('mfa_setup'),
            data={'code': current_totp(device), 'next': reverse('dashboard')},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))

    def test_backup_code_verifies_once_then_fails_on_reuse(self):
        self._login_password(self.admin_user)

        # Confirmed TOTP device -> the gate is in "verify" mode. Plus a backup
        # device holding a single known static token.
        TOTPDevice.objects.create(user=self.admin_user, name='default', confirmed=True)
        backup_device = StaticDevice.objects.create(user=self.admin_user, name='backup')
        backup_code = StaticToken.random_token()
        backup_device.token_set.create(token=backup_code)

        # First use: backup code verifies -> redirect to dashboard, token consumed.
        first = self.client.post(
            reverse('mfa_setup'),
            data={'code': backup_code, 'next': reverse('dashboard')},
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.url, reverse('dashboard'))
        self.assertEqual(backup_device.token_set.filter(token=backup_code).count(), 0)

        # Re-login fresh (unverified session) and reuse the now-consumed token.
        self._login_password(self.admin_user)
        second = self.client.post(
            reverse('mfa_setup'),
            data={'code': backup_code, 'next': reverse('dashboard')},
        )
        # No match: the gate re-renders with an error (HTTP 200, no redirect).
        self.assertEqual(second.status_code, 200)
