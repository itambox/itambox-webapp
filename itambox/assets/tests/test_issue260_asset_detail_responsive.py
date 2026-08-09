"""Issue #260 — responsive asset-detail and scan-shell regressions.

Regression coverage for removing the assignment-status and software-inventory
stat banners while preserving the underlying asset data and tab navigation.

Every assertion runs against the real view + template render path.
"""

from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetAssignment, AssetRole, AssetType, Manufacturer, StatusLabel
from core.tests.mixins import TenantTestMixin
from organization.models import AssetHolder


class _ScanInputGroupParser(HTMLParser):
    """Extract semantic facts from the real scan input group without bs4."""

    def __init__(self):
        super().__init__()
        self.group_depth = 0
        self.button_depth = 0
        self.hidden_depth = 0
        self.input_attrs = {}
        self.button_attrs = {}
        self.visible_button_text = []
        self.barcode_segment_count = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())
        if self.group_depth == 0 and tag == "div" and "input-group-lg" in classes:
            self.group_depth = 1
            return
        if not self.group_depth:
            return
        if tag == "div":
            self.group_depth += 1
        if tag == "span" and "input-group-text" in classes:
            self.barcode_segment_count += 1
        if tag == "input" and attributes.get("id") == "scan-basket-input":
            self.input_attrs = attributes
        if tag == "button" and attributes.get("id") == "basket-open-scanner-btn":
            self.button_attrs = attributes
            self.button_depth = 1
            return
        if self.button_depth:
            self.button_depth += 1
            if tag == "span" and "visually-hidden" in classes:
                self.hidden_depth = 1

    def handle_endtag(self, tag):
        if self.hidden_depth:
            self.hidden_depth -= 1
        if self.button_depth:
            self.button_depth -= 1
        elif self.group_depth and tag == "div":
            self.group_depth -= 1

    def handle_data(self, data):
        if self.button_depth and not self.hidden_depth:
            self.visible_button_text.append(data)


def _scan_input_group(response):
    parser = _ScanInputGroupParser()
    parser.feed(response.content.decode())
    return parser


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


class AssetScanMarkupTests(TenantTestMixin, TestCase):
    """AC9/AC10 — preserve scan behavior while simplifying the controls."""

    def setUp(self):
        self.setup_tenant_context(slug="i260-scan")
        self.set_active_tenant(self.tenant, self.tenant_membership)
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        self.url = reverse("assets:asset_bulk_checkin_scan")

    def test_scan_group_keeps_input_and_has_icon_only_camera_action(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        scan_group = _scan_input_group(response)
        self.assertEqual(scan_group.barcode_segment_count, 0)
        self.assertEqual(scan_group.input_attrs.get("id"), "scan-basket-input")
        self.assertEqual(scan_group.button_attrs.get("id"), "basket-open-scanner-btn")
        self.assertEqual(scan_group.button_attrs.get("aria-label"), "Camera")
        self.assertEqual(scan_group.button_attrs.get("title"), "Camera")
        self.assertEqual("".join(scan_group.visible_button_text).strip(), "")
        self.assertIn("btn-primary", scan_group.button_attrs.get("class", ""))
        self.assertIn("scan-basket-camera", scan_group.button_attrs.get("class", ""))


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
        self.assertEqual(body.count('href="/graphql/"'), 1)
        for menu_path in (
            "/user/profile/",
            "/user/notifications/",
            "/user/subscriptions/",
            "/user/preferences/",
            "/user/api-tokens/",
        ):
            self.assertGreaterEqual(body.count('href="' + menu_path + '"'), 2)

    def test_edit_action_keeps_accessible_label_for_mobile_icon_mode(self):
        body = self._body()
        self.assertIn("detail-edit-action", body)
        self.assertIn('aria-label="Edit"', body)
        self.assertIn('title="Edit"', body)
        self.assertIn("detail-edit-action__label", body)


class AssetListMobileSelectionTests(TenantTestMixin, TestCase):
    """AC8 — the selectable asset list exposes a mobile card-mode control."""

    def setUp(self):
        self.setup_tenant_context(slug="i260-list")
        self.set_active_tenant(self.tenant, self.tenant_membership)
        _asset_fixtures(self.tenant, "-l")
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def test_select_all_control_is_rendered_for_card_tables(self):
        response = self.client.get(reverse("assets:asset_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("mobile-select-all", body)
        self.assertIn('data-select-all="true"', body)
        self.assertIn("card-table", body)
