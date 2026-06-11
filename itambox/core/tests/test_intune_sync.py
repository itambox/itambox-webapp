"""
Tests for the Intune discovery sync task.

All Graph API calls are mocked — no network required.
Covers: token refresh, pagination (via IntuneClient mocks),
        match-by-serial update, create_missing on/off,
        software upsert + unique constraint, dry-run,
        per-tenant isolation.
"""

from unittest.mock import MagicMock, patch
from django.test import TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Job
from core.tests.mixins import TenantTestMixin
from assets.models import Asset, Manufacturer, AssetType, StatusLabel
from software.models import Software, InstalledSoftware

User = get_user_model()

INTUNE_CONF_A = {
    "azure_tenant_id": "aaa-111",
    "client_id": "cid-a",
    "client_secret": "secret-a",
    "create_missing": True,
    "default_status": "deployable",
    "sync_software": True,
}
INTUNE_CONF_B = {
    "azure_tenant_id": "bbb-222",
    "client_id": "cid-b",
    "client_secret": "secret-b",
    "create_missing": True,
    "default_status": "deployable",
    "sync_software": True,
}

FAKE_DEVICE = {
    "id": "dev-1",
    "deviceName": "LAPTOP-001",
    "serialNumber": "SN12345",
    "manufacturer": "Dell",
    "model": "XPS 15",
    "operatingSystem": "Windows",
    "osVersion": "10.0.22621",
    "userPrincipalName": "alice@contoso.com",
    "lastSyncDateTime": "2024-01-01T00:00:00Z",
    "totalStorageSpaceInBytes": 512000000000,
}

FAKE_APP = {
    "displayName": "Google Chrome",
    "publisher": "Google LLC",
    "version": "121.0.6167.85",
}


def _make_job(tenant_slug="tenant-a"):
    return Job.objects.create(name=f"intune-sync:{tenant_slug}")


def _make_status(slug="deployable", name="Deployable"):
    s, _ = StatusLabel.objects.get_or_create(slug=slug, defaults={"name": name, "slug": slug})
    return s


class IntuneTokenRefreshTest(TransactionTestCase):
    """Unit-test the Graph client token logic in isolation."""

    def test_token_cached_until_near_expiry(self):
        """Second call within the cache window returns the same token without a new POST."""
        with patch("core.integrations.intune.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"access_token": "tok-1", "expires_in": 3600},
            )
            mock_post.return_value.raise_for_status = MagicMock()

            from core.integrations.intune import _get_token, _TOKEN_CACHE
            _TOKEN_CACHE.clear()

            t1 = _get_token("tenant-x", "cid", "secret")
            t2 = _get_token("tenant-x", "cid", "secret")

            self.assertEqual(t1, t2)
            self.assertEqual(mock_post.call_count, 1)

    def test_expired_token_refreshed(self):
        """A token past its expiry triggers a fresh POST."""
        import time
        from core.integrations.intune import _get_token, _TOKEN_CACHE
        _TOKEN_CACHE["tenant-y"] = {"token": "old-tok", "expires_at": time.monotonic() - 1}

        with patch("core.integrations.intune.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"access_token": "new-tok", "expires_in": 3600},
            )
            mock_post.return_value.raise_for_status = MagicMock()

            tok = _get_token("tenant-y", "cid", "secret")
            self.assertEqual(tok, "new-tok")
            mock_post.assert_called_once()


class IntunePaginationTest(TransactionTestCase):
    """IntuneClient follows @odata.nextLink correctly."""

    def test_pagination_followed(self):
        page1 = {"value": [{"id": "d1"}], "@odata.nextLink": "https://graph/page2"}
        page2 = {"value": [{"id": "d2"}], "@odata.nextLink": None}

        responses = [
            MagicMock(status_code=200, json=lambda p=p: p)
            for p in [page1, page2]
        ]
        for r in responses:
            r.raise_for_status = MagicMock()

        with patch("core.integrations.intune.requests.get", side_effect=responses):
            with patch("core.integrations.intune._get_token", return_value="tok"):
                from core.integrations.intune import IntuneClient
                client = IntuneClient("tid", "cid", "sec")
                # patch nextLink iteration: second response has no nextLink key
                # simulate by returning None from @odata.nextLink
                # We'll patch _graph_get_paginated directly instead:
                pass

        # Direct test of _graph_get_paginated
        import core.integrations.intune as intune_mod

        call_count = 0
        def fake_get(url, headers, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                r = MagicMock()
                r.status_code = 200
                r.json.return_value = {"value": [{"id": "d1"}], "@odata.nextLink": "https://graph/page2"}
                r.raise_for_status = MagicMock()
                return r
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"value": [{"id": "d2"}]}
            r.raise_for_status = MagicMock()
            return r

        with patch.object(intune_mod.requests, "get", side_effect=fake_get):
            items = intune_mod._graph_get_paginated("https://graph/page1", {})

        self.assertEqual([i["id"] for i in items], ["d1", "d2"])
        self.assertEqual(call_count, 2)


MOCK_SETTINGS = {
    "tenant-a": INTUNE_CONF_A,
    "tenant-b": INTUNE_CONF_B,
}


class IntuneSyncMatchUpdateTest(TenantTestMixin, TransactionTestCase):
    """Matched asset gets custom_field_data stamped; no new asset created."""

    def setUp(self):
        super().setUp()
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")
        _make_status()

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS=MOCK_SETTINGS)
    @patch("core.tasks.intune_sync.IntuneClient")
    def test_match_updates_discovery_facts(self, MockClient):
        mfr = Manufacturer.objects.create(name="Dell", slug="dell")
        atype = AssetType.objects.create(manufacturer=mfr, model="XPS 15")
        asset = Asset.objects.create(
            name="Old Name",
            serial_number="SN12345",
            asset_type=atype,
            tenant=self.tenant,
        )

        instance = MockClient.return_value
        instance.get_managed_devices.return_value = [FAKE_DEVICE]
        instance.get_detected_apps.return_value = []

        job = _make_job("tenant-a")
        from core.tasks.intune_sync import sync_tenant_intune
        sync_tenant_intune(
            tenant_id=self.tenant.pk,
            user_id=self.tenant_admin.pk,
            job_id=job.pk,
            dry_run=False,
        )

        asset.refresh_from_db()
        job.refresh_from_db()

        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        self.assertEqual(asset.custom_field_data["intune_device_id"], "dev-1")
        self.assertEqual(asset.custom_field_data["os_version"], "10.0.22621")
        self.assertEqual(asset.custom_field_data["intune_primary_user"], "alice@contoso.com")
        # No new assets should have been created
        self.assertEqual(Asset.objects.filter(tenant=self.tenant).count(), 1)
        # Counts reported correctly
        self.assertEqual(job.result["matched"], 1)
        self.assertEqual(job.result["created"], 0)


class IntuneSyncCreateMissingTest(TenantTestMixin, TransactionTestCase):
    """Unmatched device creates Manufacturer, AssetType, Asset when create_missing=True."""

    def setUp(self):
        super().setUp()
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")
        _make_status()

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS=MOCK_SETTINGS)
    @patch("core.tasks.intune_sync.IntuneClient")
    def test_create_missing_true(self, MockClient):
        instance = MockClient.return_value
        instance.get_managed_devices.return_value = [FAKE_DEVICE]
        instance.get_detected_apps.return_value = []

        job = _make_job("tenant-a")
        from core.tasks.intune_sync import sync_tenant_intune
        sync_tenant_intune(
            tenant_id=self.tenant.pk,
            user_id=self.tenant_admin.pk,
            job_id=job.pk,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        self.assertEqual(job.result["created"], 1)
        asset = Asset.objects.get(tenant=self.tenant, serial_number="SN12345")
        self.assertEqual(asset.name, "LAPTOP-001")
        self.assertTrue(Manufacturer.objects.filter(name="Dell").exists())
        self.assertTrue(AssetType.objects.filter(model="XPS 15").exists())

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS={
        "tenant-a": {**INTUNE_CONF_A, "create_missing": False}
    })
    @patch("core.tasks.intune_sync.IntuneClient")
    def test_create_missing_false_skips(self, MockClient):
        instance = MockClient.return_value
        instance.get_managed_devices.return_value = [FAKE_DEVICE]
        instance.get_detected_apps.return_value = []

        job = _make_job("tenant-a")
        from core.tasks.intune_sync import sync_tenant_intune
        sync_tenant_intune(
            tenant_id=self.tenant.pk,
            user_id=self.tenant_admin.pk,
            job_id=job.pk,
        )

        job.refresh_from_db()
        self.assertEqual(job.result["created"], 0)
        self.assertEqual(job.result["skipped"], 1)
        self.assertFalse(Asset.objects.filter(serial_number="SN12345").exists())


class IntuneSyncSoftwareTest(TenantTestMixin, TransactionTestCase):
    """Software upsert creates Software + InstalledSoftware; re-run respects unique constraint."""

    def setUp(self):
        super().setUp()
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")
        _make_status()

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS=MOCK_SETTINGS)
    @patch("core.tasks.intune_sync.IntuneClient")
    def test_software_upserted(self, MockClient):
        mfr = Manufacturer.objects.create(name="Dell", slug="dell")
        atype = AssetType.objects.create(manufacturer=mfr, model="XPS 15")
        asset = Asset.objects.create(
            name="Laptop",
            serial_number="SN12345",
            asset_type=atype,
            tenant=self.tenant,
        )

        instance = MockClient.return_value
        instance.get_managed_devices.return_value = [FAKE_DEVICE]
        instance.get_detected_apps.return_value = [FAKE_APP]

        job = _make_job("tenant-a")
        from core.tasks.intune_sync import sync_tenant_intune
        sync_tenant_intune(
            tenant_id=self.tenant.pk,
            user_id=self.tenant_admin.pk,
            job_id=job.pk,
        )

        job.refresh_from_db()
        self.assertEqual(job.result["apps_upserted"], 1)
        self.assertTrue(Software.objects.filter(name="Google Chrome").exists())
        self.assertTrue(InstalledSoftware.objects.filter(
            asset=asset,
            software__name="Google Chrome",
            version_detected="121.0.6167.85",
            discovered_by_agent="Intune",
        ).exists())

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS=MOCK_SETTINGS)
    @patch("core.tasks.intune_sync.IntuneClient")
    def test_software_rerun_no_duplicate(self, MockClient):
        """Second sync run must not raise on the unique_asset_software_version constraint."""
        mfr = Manufacturer.objects.create(name="Dell", slug="dell")
        atype = AssetType.objects.create(manufacturer=mfr, model="XPS 15")
        asset = Asset.objects.create(
            name="Laptop",
            serial_number="SN12345",
            asset_type=atype,
            tenant=self.tenant,
        )

        instance = MockClient.return_value
        instance.get_managed_devices.return_value = [FAKE_DEVICE]
        instance.get_detected_apps.return_value = [FAKE_APP]

        from core.tasks.intune_sync import sync_tenant_intune

        job1 = _make_job("tenant-a")
        sync_tenant_intune(tenant_id=self.tenant.pk, user_id=self.tenant_admin.pk, job_id=job1.pk)
        job1.refresh_from_db()
        self.assertEqual(job1.status, Job.STATUS_COMPLETED)

        job2 = _make_job("tenant-a")
        sync_tenant_intune(tenant_id=self.tenant.pk, user_id=self.tenant_admin.pk, job_id=job2.pk)
        job2.refresh_from_db()
        self.assertEqual(job2.status, Job.STATUS_COMPLETED)

        # Still exactly one InstalledSoftware record
        self.assertEqual(
            InstalledSoftware.objects.filter(asset=asset, software__name="Google Chrome").count(),
            1,
        )


class IntuneSyncDryRunTest(TenantTestMixin, TransactionTestCase):
    """Dry-run must not write any DB records."""

    def setUp(self):
        super().setUp()
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")
        _make_status()

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS=MOCK_SETTINGS)
    @patch("core.tasks.intune_sync.IntuneClient")
    def test_dry_run_no_writes(self, MockClient):
        instance = MockClient.return_value
        instance.get_managed_devices.return_value = [FAKE_DEVICE]
        instance.get_detected_apps.return_value = [FAKE_APP]

        job = _make_job("tenant-a")
        from core.tasks.intune_sync import sync_tenant_intune
        sync_tenant_intune(
            tenant_id=self.tenant.pk,
            user_id=self.tenant_admin.pk,
            job_id=job.pk,
            dry_run=True,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        # No Asset or Software rows created
        self.assertFalse(Asset.objects.filter(tenant=self.tenant).exists())
        self.assertFalse(Software.objects.all().exists())


class IntuneSyncTenantIsolationTest(TenantTestMixin, TransactionTestCase):
    """Objects land in the correct tenant when two tenants are configured."""

    def setUp(self):
        super().setUp()
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")
        _make_status()

        # Second tenant
        from organization.models import Tenant
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.admin_b = User.objects.create_superuser(
            username="admin_b", email="admin_b@example.com", password="pw"
        )

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS=MOCK_SETTINGS)
    @patch("core.tasks.intune_sync.IntuneClient")
    def test_assets_land_in_correct_tenant(self, MockClient):
        device_a = {**FAKE_DEVICE, "id": "dev-a", "serialNumber": "SNA001", "deviceName": "A-LAPTOP"}
        device_b = {**FAKE_DEVICE, "id": "dev-b", "serialNumber": "SNB001", "deviceName": "B-LAPTOP"}

        from core.tasks.intune_sync import sync_tenant_intune

        # Sync for tenant A
        instance = MockClient.return_value
        instance.get_managed_devices.return_value = [device_a]
        instance.get_detected_apps.return_value = []
        job_a = _make_job("tenant-a")
        sync_tenant_intune(tenant_id=self.tenant.pk, user_id=self.tenant_admin.pk, job_id=job_a.pk)

        # Sync for tenant B
        instance.get_managed_devices.return_value = [device_b]
        job_b = _make_job("tenant-b")
        sync_tenant_intune(tenant_id=self.tenant_b.pk, user_id=self.admin_b.pk, job_id=job_b.pk)

        asset_a = Asset.objects.get(serial_number="SNA001")
        asset_b = Asset.objects.get(serial_number="SNB001")

        self.assertEqual(asset_a.tenant, self.tenant)
        self.assertEqual(asset_b.tenant, self.tenant_b)
        # Cross-check: no bleed
        self.assertNotEqual(asset_a.tenant, self.tenant_b)
        self.assertNotEqual(asset_b.tenant, self.tenant)
