"""Regression tests for Issue #389 accessible names on icon-only controls."""

from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetRole, AssetType, Manufacturer, StatusLabel, Supplier
from core.tests.mixins import TenantTestMixin
from extras.models import FileAttachment
from licenses.models import License
from organization.models import Location, Site
from procurement.models import PurchaseOrder, PurchaseOrderLine
from software.models import Software


class _RoleTabParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tabs = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("role") == "tab":
            self.tabs.append(attributes)


class AssetDetailAccessibleNameTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(slug="issue389-accessible-tabs")
        self.set_active_tenant(self.tenant, self.tenant_membership)

        manufacturer = Manufacturer.objects.create(name="Issue 389 Manufacturer", slug="issue389-manufacturer")
        role = AssetRole.objects.create(name="Issue 389 Role", slug="issue389-role")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Issue 389 Model",
            slug="issue389-model",
        )
        status = StatusLabel.objects.create(
            name="Issue 389 Deployable",
            slug="issue389-deployable",
            type="deployable",
        )
        self.asset = Asset.objects.create(
            name="Issue 389 Asset",
            asset_tag="ISSUE389-ASSET",
            serial_number="ISSUE389-SERIAL",
            asset_type=asset_type,
            asset_role=role,
            status=status,
            tenant=self.tenant,
        )
        content_type = ContentType.objects.get_for_model(Asset)
        FileAttachment.objects.create(
            model=content_type,
            object_id=self.asset.pk,
            file=SimpleUploadedFile("issue389.txt", b"Issue 389 attachment", content_type="text/plain"),
            name="Issue 389 attachment",
            mime_type="text/plain",
        )
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def test_detail_tabs_have_stable_accessible_names(self):
        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))

        self.assertEqual(response.status_code, 200)
        parser = _RoleTabParser()
        parser.feed(response.content.decode())
        self.assertGreaterEqual(len(parser.tabs), 3)
        self.assertTrue(all(tab.get("aria-label") for tab in parser.tabs), parser.tabs)
        self.assertContains(response, 'aria-label="Details"')
        self.assertContains(response, 'aria-label="Attachments"')
        self.assertContains(response, 'aria-label="Changelog"')


class PurchaseOrderLineAccessibleNameTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(slug="issue389-accessible-po-lines")
        self.set_active_tenant(self.tenant, self.tenant_membership)

        site = Site.objects.create(name="Issue 389 Site", slug="issue389-site")
        location = Location.objects.create(
            name="Issue 389 Location",
            slug="issue389-location",
            site=site,
            tenant=self.tenant,
        )
        supplier = Supplier.objects.create(name="Issue 389 Supplier", slug="issue389-supplier")
        manufacturer = Manufacturer.objects.create(name="Issue 389 Software Vendor", slug="issue389-software-vendor")
        software = Software.objects.create(
            name="Issue 389 Software",
            manufacturer=manufacturer,
            tenant=self.tenant,
        )
        license_record = License.objects.create(
            name="Issue 389 License",
            software=software,
            seats=5,
            currency="EUR",
            tenant=self.tenant,
            supplier=supplier,
        )
        purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="ISSUE389-PO-001",
            currency="EUR",
            supplier=supplier,
            status=PurchaseOrder.STATUS_DRAFT,
            destination_location=location,
            created_by=self.tenant_admin,
        )
        self.line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            license=license_record,
            qty_ordered=2,
            unit_price=Decimal("25.00"),
        )
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def test_icon_only_line_actions_have_stable_accessible_names(self):
        response = self.client.get(
            reverse("procurement:purchaseorder_detail", kwargs={"pk": self.line.purchase_order_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Edit line item"')
        self.assertContains(response, 'aria-label="Delete line item"')
