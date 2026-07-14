"""Regression tests for the security-review mitigations."""
import socket
import uuid
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from model_bakery import baker

from core.http import request_pinned
from core.managers import set_current_tenant
from core.models import ObjectChange
from core.validators import validate_external_url
from organization.models import Tenant


class SSRFValidatorTests(TestCase):
    """validate_external_url must reject internal/loopback/metadata targets."""

    def test_allows_public_https(self):
        # example.com resolves to public addresses.
        validate_external_url('https://example.com/hook')

    def test_rejects_non_http_scheme(self):
        for url in ('ftp://example.com', 'file:///etc/passwd', 'gopher://x'):
            with self.assertRaises(ValidationError):
                validate_external_url(url)

    def test_rejects_loopback(self):
        for url in ('http://127.0.0.1/x', 'http://localhost/x', 'http://[::1]/x'):
            with self.assertRaises(ValidationError):
                validate_external_url(url)

    def test_rejects_link_local_metadata(self):
        # AWS/GCP/Azure instance metadata endpoint.
        with self.assertRaises(ValidationError):
            validate_external_url('http://169.254.169.254/latest/meta-data/')

    def test_rejects_private_ranges(self):
        for url in ('http://10.0.0.5/x', 'http://192.168.1.1/x', 'http://172.16.0.1/x'):
            with self.assertRaises(ValidationError):
                validate_external_url(url)

    def test_rejects_empty_and_hostless(self):
        with self.assertRaises(ValidationError):
            validate_external_url('')
        with self.assertRaises(ValidationError):
            validate_external_url('http:///nohost')

    @patch('socket.getaddrinfo')
    def test_dns_resolution_failure_fails_closed(self, mock_getaddrinfo):
        """Release blocker #1: an unresolvable host must be REJECTED, not silently
        allowed through. A resolver error (transient outage, or an attacker's DNS
        answering differently at validation time vs. send time) used to return an
        empty result and let the URL through; it must now fail closed."""
        mock_getaddrinfo.side_effect = socket.gaierror()
        with self.assertRaises(ValidationError):
            validate_external_url('https://doesnotexist.invalid/x')


class RequestPinnedTests(TestCase):
    """core.http.request_pinned: DNS-rebinding-safe outbound sender.

    Connects to the FIRST resolved address (not the hostname) while preserving the
    hostname as the Host header / TLS SNI target, and never follows redirects."""

    def _make_pinned_response(self, status_code=200):
        # A response returned by a mocked HTTPAdapter.send bypasses real urllib3
        # machinery, so requests.Session.send's own post-processing (redirect
        # resolution, cookie extraction) must be steered away from treating
        # MagicMock auto-attributes as real values: is_redirect must be falsy
        # (else Session.send tries to parse a MagicMock as a redirect Location),
        # and raw must be falsy (else cookie extraction tries to read headers off
        # a MagicMock and blows up in the stdlib http.cookiejar internals).
        resp = MagicMock(status_code=status_code)
        resp.is_redirect = False
        resp.raw = None
        return resp

    @patch('requests.adapters.HTTPAdapter.send')
    @patch('socket.getaddrinfo')
    def test_pinned_to_resolved_ip_with_original_host_header(self, mock_getaddrinfo, mock_send):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', 443)),
        ]
        mock_send.return_value = self._make_pinned_response()

        request_pinned('POST', 'https://hooks.example.com/x', json={})

        self.assertEqual(mock_send.call_count, 1)
        sent_request = mock_send.call_args[0][0]
        # The socket target is the resolved IP, not the hostname...
        self.assertEqual(urlsplit(sent_request.url).netloc, '93.184.216.34')
        # ...but the server still sees the original hostname via the Host header.
        self.assertEqual(sent_request.headers['Host'], 'hooks.example.com')

    @patch('requests.adapters.HTTPAdapter.send')
    @patch('socket.getaddrinfo')
    def test_pinned_ipv6_address_uses_bracketed_form(self, mock_getaddrinfo, mock_send):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
             ('2606:2800:220:1:248:1893:25c8:1946', 443, 0, 0)),
        ]
        mock_send.return_value = self._make_pinned_response()

        request_pinned('POST', 'https://hooks.example.com/x', json={})

        sent_request = mock_send.call_args[0][0]
        self.assertEqual(
            urlsplit(sent_request.url).netloc, '[2606:2800:220:1:248:1893:25c8:1946]',
        )
        self.assertEqual(sent_request.headers['Host'], 'hooks.example.com')

    @patch('requests.Session.request')
    def test_redirects_are_never_followed(self, mock_session_request):
        """A 3xx pointing at an internal address would bypass the pinning guard
        entirely, so redirects must be disabled at the requests layer. Asserted
        directly on what core.http passes to Session.request (allow_redirects is
        popped by Session.send before reaching the adapter, so it can't be
        observed at the HTTPAdapter.send seam used by the other tests here)."""
        mock_session_request.return_value = self._make_pinned_response()

        # A public IP literal skips DNS entirely, keeping this test focused on the
        # redirect flag rather than resolution.
        request_pinned('POST', 'https://93.184.216.34/x', json={})

        self.assertEqual(mock_session_request.call_count, 1)
        _, kwargs = mock_session_request.call_args
        self.assertIs(kwargs['allow_redirects'], False)


class ChangelogTenantScopingTests(TestCase):
    """C3: ObjectChange must be scoped to the active tenant."""

    def setUp(self):
        self.ta = Tenant.objects.create(name='Iso A', slug='iso-a')
        self.tb = Tenant.objects.create(name='Iso B', slug='iso-b')
        self.ct = ContentType.objects.get_for_model(Tenant)
        self.change_a = self._make_change(self.ta)
        self.change_b = self._make_change(self.tb)

    def _make_change(self, tenant):
        return ObjectChange._base_manager.create(
            tenant=tenant,
            user=None,
            user_name='System',
            request_id=uuid.uuid4(),
            action='create',
            changed_object_type=self.ct,
            changed_object_id=tenant.pk,
            object_repr=str(tenant),
        )

    def tearDown(self):
        set_current_tenant(None)

    def test_changelog_scoped_to_active_tenant(self):
        set_current_tenant(self.ta)
        pks = set(ObjectChange.objects.values_list('pk', flat=True))
        self.assertIn(self.change_a.pk, pks)
        self.assertNotIn(self.change_b.pk, pks, "Tenant A must not see Tenant B's change history")

    def test_other_tenant_change_not_retrievable(self):
        set_current_tenant(self.ta)
        with self.assertRaises(ObjectChange.DoesNotExist):
            ObjectChange.objects.get(pk=self.change_b.pk)


class AssignmentTenantScopingTests(TestCase):
    """C4: assignment rows must not leak/IDOR across tenants via the manager."""

    def setUp(self):
        self.ta = Tenant.objects.create(name='Asg A', slug='asg-a')
        self.tb = Tenant.objects.create(name='Asg B', slug='asg-b')

    def tearDown(self):
        set_current_tenant(None)

    def test_asset_assignment_scoped_by_parent_tenant(self):
        from assets.models import Asset, AssetAssignment
        set_current_tenant(None)
        asset_a = baker.make(Asset, tenant=self.ta)
        asset_b = baker.make(Asset, tenant=self.tb)
        asgn_a = baker.make(AssetAssignment, asset=asset_a, is_active=False)
        asgn_b = baker.make(AssetAssignment, asset=asset_b, is_active=False)

        set_current_tenant(self.ta)
        pks = set(AssetAssignment.objects.values_list('pk', flat=True))
        self.assertIn(asgn_a.pk, pks)
        self.assertNotIn(asgn_b.pk, pks, "Tenant A must not list Tenant B's assignments")
        with self.assertRaises(AssetAssignment.DoesNotExist):
            AssetAssignment.objects.get(pk=asgn_b.pk)

    def test_assignment_tenant_property_resolves_parent(self):
        from assets.models import Asset, AssetAssignment
        asset_a = baker.make(Asset, tenant=self.ta)
        asgn = baker.make(AssetAssignment, asset=asset_a, is_active=False)
        self.assertEqual(asgn.tenant, self.ta)
