"""Integration tests for the TOTP MFA gate (local-password logins only).

Covers the policy helpers (``core.mfa``), the enforcement middleware
(``core.otp_middleware.OTPEnforcementMiddleware``) and the enroll/verify gate
view (``core.views.mfa.MFASetupView``).

MFA is enforced only for *local password* sessions of superusers / holders of a
*privileged* role. Privilege keys on the role's ``permissions`` JSON (anything
other than a read-only ``view_*`` capability) and the canonical privileged role names
(``Admin``/``Manager``), never on a role-name regex — so a ``Manager`` and any
custom role granting writes are covered, while a read-only ``Viewer`` is not.
SSO/LDAP/SAML/OIDC sessions set a different ``_auth_user_backend`` on the session
and are exempt; token-API requests have no session backend.

This suite is order-dependent: fixtures use the ``-mfa`` suffix and unique names
so they never collide with the rest of the security suite.
"""
from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from organization.models import Tenant, Role, Membership
from core.mfa import user_requires_mfa, request_needs_mfa, PASSWORD_BACKEND
from core.tests.mixins import grant

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
    """user_requires_mfa(): superuser / privileged role -> True; read-only -> False.

    Privilege is keyed on the canonical privileged role names (Admin/Manager)
    and on the ``permissions`` JSON (any non-view grant), NOT a role
    name regex. The legacy ``^(admin|owner)$`` regex let a fully-permissioned
    ``Manager`` (the role SSO auto-provisions) bypass MFA — these tests pin the
    privilege-based behaviour.
    """

    # A representative set of mutating perms in the stored "<app>.<codename>"
    # shape (as the SSO backends' get_permissions_for_role emits).
    WRITE_PERMS = ['assets.view_asset', 'assets.add_asset', 'assets.change_asset']
    READ_ONLY_PERMS = ['assets.view_asset', 'extras.view_dashboard']

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Policy Tenant MFA', slug='policy-tenant-mfa')

        self.super_user = User.objects.create_superuser(
            username='super-mfa', email='super-mfa@example.com', password='pw-mfa',
        )

        # H4: a Manager is privileged by canonical name (SSO auto-provisions it
        # with write perms). The old regex did NOT match 'Manager'.
        self.manager_user = User.objects.create_user(
            username='manager-mfa', email='manager-mfa@example.com', password='pw-mfa',
        )
        manager_role = Role.objects.create(
            tenant=self.tenant, name='Manager', permissions=self.WRITE_PERMS,
        )
        m_manager = grant(self.manager_user, self.tenant, manager_role).membership

        # Admin: privileged by canonical name.
        self.admin_user = User.objects.create_user(
            username='admin-mfa', email='admin-mfa@example.com', password='pw-mfa',
        )
        admin_role = Role.objects.create(
            tenant=self.tenant, name='Admin',
            permissions=self.WRITE_PERMS + ['assets.delete_asset'],
        )
        m_admin = grant(self.admin_user, self.tenant, admin_role).membership

        # H4: a custom-named role that is NOT a canonical name but grants a
        # non-view permission is privileged by its permissions.
        self.custom_user = User.objects.create_user(
            username='custom-mfa', email='custom-mfa@example.com', password='pw-mfa',
        )
        custom_role = Role.objects.create(
            tenant=self.tenant, name='Fleet Steward', permissions=self.WRITE_PERMS,
        )
        m_custom = grant(self.custom_user, self.tenant, custom_role).membership

        # A read-only Viewer: neither a privileged name nor any mutating perm.
        self.viewer_user = User.objects.create_user(
            username='viewer-mfa', email='viewer-mfa@example.com', password='pw-mfa',
        )
        viewer_role = Role.objects.create(
            tenant=self.tenant, name='Viewer', permissions=self.READ_ONLY_PERMS,
        )
        m_viewer = grant(self.viewer_user, self.tenant, viewer_role).membership

    def test_superuser_requires_mfa(self):
        self.assertTrue(user_requires_mfa(self.super_user))

    def test_manager_role_requires_mfa(self):
        # The key H4 regression: 'Manager' must require MFA.
        self.assertTrue(user_requires_mfa(self.manager_user))

    def test_admin_role_requires_mfa(self):
        self.assertTrue(user_requires_mfa(self.admin_user))

    def test_custom_role_with_write_perms_requires_mfa(self):
        self.assertTrue(user_requires_mfa(self.custom_user))

    def test_readonly_viewer_does_not_require_mfa(self):
        self.assertFalse(user_requires_mfa(self.viewer_user))


class MFAPolicyNoStaleCacheTests(TestCase):
    """M3: request_needs_mfa computes the policy per request, no session cache.

    Asserts directly against request_needs_mfa with a simulated password-login
    session so the guarantee holds independently of the middleware/dashboard.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(
            name='No Cache Tenant MFA', slug='no-cache-tenant-mfa',
        )
        self.user = User.objects.create_user(
            username='nocache-mfa', email='nocache-mfa@example.com', password='pw-mfa',
        )
        self.viewer_role = Role.objects.create(
            tenant=self.tenant, name='Viewer', permissions=['assets.view_asset'],
        )
        self.membership = grant(self.user, self.tenant, self.viewer_role).membership

    def _password_request(self):
        request = self.factory.get('/')
        request.user = self.user
        # A dict is enough: request_needs_mfa only reads/writes session keys.
        request.session = {'_auth_user_backend': PASSWORD_BACKEND}
        return request

    def test_request_needs_mfa_reflects_midsession_upgrade(self):
        request = self._password_request()

        # Read-only Viewer: not gated.
        self.assertFalse(request_needs_mfa(request))
        # No policy result is cached in the session (M3 regression guard).
        self.assertNotIn('mfa_required', request.session)

        # Promote the role's permissions in place (mid-session, same request obj).
        self.viewer_role.permissions = ['assets.view_asset', 'assets.change_asset']
        self.viewer_role.save()

        # Recomputed fresh -> now gated, no re-login / new session needed.
        self.assertTrue(request_needs_mfa(request))
        self.assertNotIn('mfa_required', request.session)


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
        admin_role = Role.objects.create(tenant=self.tenant, name='Admin', permissions=[])
        m_admin = grant(self.admin_user, self.tenant, admin_role).membership

        # H4: a Manager (the SSO-provisioned privileged role) must be gated too.
        self.manager_user = User.objects.create_user(
            username='enforce-manager-mfa', email='enforce-manager-mfa@example.com', password='pw-mfa',
        )
        manager_role = Role.objects.create(
            tenant=self.tenant, name='Manager',
            permissions=['assets.view_asset', 'assets.add_asset', 'assets.change_asset'],
        )
        m_manager = grant(self.manager_user, self.tenant, manager_role).membership

        self.member_user = User.objects.create_user(
            username='enforce-member-mfa', email='enforce-member-mfa@example.com', password='pw-mfa',
        )
        # Read-only role: not privileged by name and no mutating perms -> exempt.
        member_role = Role.objects.create(
            tenant=self.tenant, name='Member', permissions=['assets.view_asset'],
        )
        m_member = grant(self.member_user, self.tenant, member_role).membership

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

    def test_password_manager_unverified_is_redirected_to_mfa(self):
        # H4 regression through the middleware: the legacy ^(admin|owner)$ regex
        # never matched 'Manager', so a Manager skipped the gate entirely.
        self._login_with_backend(self.manager_user, PASSWORD_BACKEND)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('mfa_setup'))

    def test_password_member_is_not_redirected_to_mfa(self):
        self._login_with_backend(self.member_user, PASSWORD_BACKEND)

        response = self.client.get(reverse('dashboard'))

        # A read-only password user is exempt: whatever the dashboard does
        # (render or its own redirect), it is never the MFA gate.
        if response.status_code == 302:
            self.assertNotEqual(response.url, reverse('mfa_setup'))

    def test_sso_admin_is_not_redirected_to_mfa(self):
        # Same admin, but an SSO (OIDC) backend on the session -> exempt.
        self._login_with_backend(self.admin_user, SSO_BACKEND)

        response = self.client.get(reverse('dashboard'))

        if response.status_code == 302:
            self.assertNotEqual(response.url, reverse('mfa_setup'))

    def test_midsession_role_upgrade_is_enforced_without_relogin(self):
        # M3: the policy was cached in session['mfa_required'] and never
        # invalidated, so a member promoted mid-session ran 8h with no second
        # factor. The cache is gone; the upgrade takes effect on the next request.
        self._login_with_backend(self.member_user, PASSWORD_BACKEND)

        # Before the upgrade: read-only member is not gated.
        first = self.client.get(reverse('dashboard'))
        if first.status_code == 302:
            self.assertNotEqual(first.url, reverse('mfa_setup'))

        # Promote the member to a privileged role (no re-login, same session).
        privileged = Role.objects.create(
            tenant=self.tenant, name='Promoted',
            permissions=['assets.view_asset', 'assets.change_asset'],
        )
        membership = Membership.objects.get(
            user=self.member_user, tenant=self.tenant,
        )
        membership.role_grants.all().delete()
        grant(self.member_user, self.tenant, privileged)

        # Next request on the SAME session is now gated.
        after = self.client.get(reverse('dashboard'))
        self.assertEqual(after.status_code, 302)
        self.assertEqual(after.url, reverse('mfa_setup'))


class MFAAllowlistTests(TestCase):
    """L3: the OTP allowlist must not blanket-exempt all of `/accounts/`.

    A password-authenticated but MFA-unverified user must NOT reach
    self-service `password_change` before completing MFA, while the genuine
    pre-MFA auth flow (login/logout/password_reset) stays reachable.
    """

    def _allowlisted(self, path):
        from core.otp_middleware import OTPEnforcementMiddleware
        mw = OTPEnforcementMiddleware(lambda r: None)
        mw._mfa_path = reverse('mfa_setup')
        return any(path.startswith(p) for p in mw._allowlist())

    def test_password_change_is_not_allowlisted(self):
        self.assertFalse(self._allowlisted('/accounts/password_change/'))
        self.assertFalse(self._allowlisted('/accounts/password_change/done/'))

    def test_auth_flow_paths_are_allowlisted(self):
        self.assertTrue(self._allowlisted('/accounts/login/'))
        self.assertTrue(self._allowlisted('/accounts/logout/'))
        self.assertTrue(self._allowlisted('/accounts/password_reset/'))
        self.assertTrue(self._allowlisted('/accounts/password_reset/done/'))
        self.assertTrue(self._allowlisted('/accounts/reset/MQ/set-token/'))
        self.assertTrue(self._allowlisted(reverse('mfa_setup')))


@override_settings(MFA_ENFORCED=True)
class MFAGateViewTests(TestCase):
    """MFASetupView enroll/verify/backup-code flows on a password session."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Gate Tenant MFA', slug='gate-tenant-mfa')
        self.admin_user = User.objects.create_user(
            username='gate-admin-mfa', email='gate-admin-mfa@example.com', password='pw-mfa',
        )
        admin_role = Role.objects.create(tenant=self.tenant, name='Admin', permissions=[])
        m_admin = grant(self.admin_user, self.tenant, admin_role).membership

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
