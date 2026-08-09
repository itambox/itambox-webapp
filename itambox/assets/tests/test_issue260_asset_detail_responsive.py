"""Issue #260 — redundant asset-detail overview banners.

Regression coverage for removing the assignment-status and software-inventory
stat banners while preserving the underlying asset data and tab navigation.

Every assertion runs against the real view + template render path.
"""

from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetAssignment, AssetRole, AssetType, Manufacturer, StatusLabel
from core.tests.mixins import TenantTestMixin
from organization.models import AssetHolder


def _asset_fixtures(tenant, suffix=""):
    manufacturer = Manufacturer.objects.create(name=f"Mfr{suffix}", slug=f"mfr{suffix}")
    role = AssetRole.objects.create(name=f"Role{suffix}", slug=f"role{suffix}")
    asset_type = AssetType.objects.create(manufacturer=manufacturer, model=f"Model{suffix}", slug=f"type{suffix}")
    status = StatusLabel.objects.create(name=f"Deployable{suffix}", slug=f"deployable{suffix}", type="deployable")
    return Asset.objects.create(
        name=f"Detail Asset{suffix}",
        asset_tag=f"DET-{suffix or '001'}",
        serial_number=f"SN-DET-{suffix or '001'}",
        asset_type=asset_type,
        asset_role=role,
        status=status,
        tenant=tenant,
    )


class AssetDetailBannerRemovalTests(TenantTestMixin, TestCase):
    """AC1/AC2/AC3 — the two informational banners are gone, the data is not."""

    def setUp(self):
        self.setup_tenant_context(slug="i260-detail")
        self.set_active_tenant(self.tenant, self.tenant_membership)
        self.asset = _asset_fixtures(self.tenant, "-d")
        self.url = reverse("assets:asset_detail", kwargs={"pk": self.asset.pk})
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def _body(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_assignment_status_banner_not_rendered(self):
        body = self._body()
        self.assertNotIn("Assignment Status", body)
        self.assertNotIn("Available in Inventory", body)

    def test_assignment_status_banner_not_rendered_for_assigned_asset(self):
        holder = AssetHolder.objects.create(
            first_name="Assigned",
            last_name="Holder",
            upn="assigned-holder-i260@example.test",
            tenant=self.tenant,
        )
        AssetAssignment.objects.create(asset=self.asset, assigned_user=holder, is_active=True)
        self.assertIsNotNone(self.asset.active_assignment)
        self.assertEqual(self.asset.active_assignment.assigned_user, holder)

        body = self._body()
        self.assertIn(str(holder), body)
        self.assertNotIn("Assignment Status", body)
        self.assertNotIn("Deployed / Checked Out", body)

    def test_software_inventory_banner_not_rendered_in_zero_state(self):
        """The empty/zero state ("0 Items Logged") must not render either."""
        self.assertEqual(self.asset.installed_software.count(), 0)
        body = self._body()
        # "Installed Software Inventory" (the Software tab heading) is legitimate
        # and must stay; only the removed banner's bare "0 Items Logged" stat is checked.
        self.assertNotIn("Items Logged", body)
        self.assertNotIn("Item Logged", body)

    def test_metrics_include_is_no_longer_used_by_the_detail_template(self):
        response = self.client.get(self.url)
        used = {template.name for template in response.templates if template.name}
        self.assertIn("assets/assets/asset_detail.html", used)
        self.assertNotIn("assets/includes/detail/asset_metrics.html", used)

    def test_detail_data_and_navigation_are_preserved(self):
        body = self._body()
        # Underlying asset data still rendered
        self.assertIn(self.asset.asset_tag, body)
        self.assertIn(self.asset.serial_number, body)
        # Deployment & Custody card (the real assignment data) still present
        self.assertIn("Deployment &amp; Custody", body)
        # Details / Maintenances / Software navigation intact
        self.assertIn('data-bs-target="#details"', body)
        self.assertIn('data-bs-target="#maintenances"', body)
        self.assertIn('data-bs-target="#software"', body)
        self.assertIn("?tab=software", body)
