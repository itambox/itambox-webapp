"""Tests for AssetRole.allows_components and Asset.is_modular."""
from django.test import TestCase

from assets.models import Asset, AssetRole, AssetType, Manufacturer, StatusLabel
from core.tests.mixins import TenantTestMixin


class AllowsComponentsMigrationBackfillTests(TenantTestMixin, TestCase):
    """Migration 0044 backfill: slug-matching roles get allows_components=True."""

    def setUp(self):
        super().setUp()
        self.setup_tenant_context()

    def test_server_slug_role_gets_allows_components(self):
        role = AssetRole.objects.create(name="Server Rack", slug="server-rack")
        # Simulate what the migration does
        from django.db.models import Q
        AssetRole.objects.filter(
            Q(slug__icontains='server') | Q(slug__icontains='modular') |
            Q(slug__icontains='workstation') | Q(slug__icontains='hypervisor')
        ).update(allows_components=True)
        role.refresh_from_db()
        self.assertTrue(role.allows_components)

    def test_workstation_slug_gets_allows_components(self):
        role = AssetRole.objects.create(name="Workstation", slug="workstation")
        from django.db.models import Q
        AssetRole.objects.filter(
            Q(slug__icontains='server') | Q(slug__icontains='modular') |
            Q(slug__icontains='workstation') | Q(slug__icontains='hypervisor')
        ).update(allows_components=True)
        role.refresh_from_db()
        self.assertTrue(role.allows_components)

    def test_unrelated_slug_not_backfilled(self):
        role = AssetRole.objects.create(name="Mobile Phone", slug="mobile-phone")
        from django.db.models import Q
        AssetRole.objects.filter(
            Q(slug__icontains='server') | Q(slug__icontains='modular') |
            Q(slug__icontains='workstation') | Q(slug__icontains='hypervisor')
        ).update(allows_components=True)
        role.refresh_from_db()
        self.assertFalse(role.allows_components)

    def test_hypervisor_slug_gets_allows_components(self):
        role = AssetRole.objects.create(name="Hypervisor Host", slug="hypervisor-host")
        from django.db.models import Q
        AssetRole.objects.filter(
            Q(slug__icontains='server') | Q(slug__icontains='modular') |
            Q(slug__icontains='workstation') | Q(slug__icontains='hypervisor')
        ).update(allows_components=True)
        role.refresh_from_db()
        self.assertTrue(role.allows_components)


class IsModularPropertyTests(TenantTestMixin, TestCase):
    """Asset.is_modular reflects allows_components flag and live allocations."""

    def setUp(self):
        super().setUp()
        self.setup_tenant_context()
        self.mfr = Manufacturer.objects.create(name="Mfr", slug="mfr-ismod")
        self.status = StatusLabel.objects.create(
            name="Active", slug="active-ismod", type=StatusLabel.TYPE_DEPLOYABLE
        )
        self.atype = AssetType.objects.create(
            manufacturer=self.mfr, model="Model", slug="model-ismod", requestable=False
        )

    def _make_asset(self, role):
        return Asset.objects.create(
            name="Test Asset",
            asset_tag="TEST-ISMOD-01",
            asset_role=role,
            asset_type=self.atype,
            status=self.status,
        )

    def test_is_modular_true_when_role_allows_components(self):
        role = AssetRole.objects.create(
            name="Server", slug="server-ismod", allows_components=True
        )
        asset = self._make_asset(role)
        self.assertTrue(asset.is_modular)

    def test_is_modular_false_when_role_does_not_allow(self):
        role = AssetRole.objects.create(
            name="Phone", slug="phone-ismod", allows_components=False
        )
        asset = self._make_asset(role)
        self.assertFalse(asset.is_modular)

    def test_is_modular_false_when_no_role(self):
        asset = Asset.objects.create(
            name="Roleless",
            asset_tag="TEST-ISMOD-02",
            asset_type=self.atype,
            status=self.status,
        )
        self.assertFalse(asset.is_modular)
