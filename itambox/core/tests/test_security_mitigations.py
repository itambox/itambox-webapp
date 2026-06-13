"""Regression tests for the security-review mitigations."""
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from model_bakery import baker

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
