"""Audit journeys: every mutation that leaves the request cycle still writes an ObjectChange."""

from io import StringIO

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest, Manufacturer, StatusLabel, Supplier
from core.models import ObjectChange
from inventory.models import Component, ComponentStock
from organization.models import Location, Site
from procurement.models import FulfillmentLink, PurchaseOrder, PurchaseOrderLine

from .support import JourneyMixin


class ReconcileProcurementLegacyAuditJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-audit")
        StatusLabel.objects.create(name="Deployable", slug="journey-audit-deployable", type="deployable")
        site = Site.objects.create(name="Journey Audit Site", slug="journey-audit-site")
        location = Location.objects.create(
            name="Journey Audit Loc", slug="journey-audit-loc", site=site, tenant=self.tenant
        )
        manufacturer = Manufacturer.objects.create(name="Journey Audit Maker", slug="journey-audit-maker")
        component = Component.objects.create(name="Journey Audit RAM", manufacturer=manufacturer)
        creator = self.make_member("audit-creator", set())
        purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-JOURNEY-AUDIT",
            currency="EUR",
            supplier=Supplier.objects.create(name="Journey Audit Supplier", slug="journey-audit-supplier"),
            destination_location=location,
            created_by=creator,
            status=PurchaseOrder.STATUS_PARTIAL,
        )
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=10,
            qty_received=2,
            unit_price="5.00",
        )
        request = AssetRequest(
            tenant=self.tenant,
            requester=creator,
            component=component,
            qty=10,
            status=RequestStatusChoices.APPROVED,
        )
        request._skip_duplicate_check = True
        request.save()
        ComponentStock.objects.create(component=component, location=location, qty=2)
        self.link = FulfillmentLink.objects.create(
            tenant=self.tenant, asset_request=request, purchase_order_line=line, qty_allocated=10
        )

    @pytest.mark.xfail(strict=True, reason="the command runs outside any change-logging context (#603)")
    def test_reconcile_procurement_legacy_apply_writes_object_changes(self):
        call_command("reconcile_procurement_legacy", "--apply", stdout=StringIO())

        closed = FulfillmentLink._base_manager.get(pk=self.link.pk)
        self.assertIsNotNone(closed.deleted_at, "precondition: the pledge was closed")
        content_type = ContentType.objects.get_for_model(FulfillmentLink)
        self.assertTrue(
            ObjectChange.objects.filter(
                changed_object_type=content_type, changed_object_id=self.link.pk, action="delete"
            ).exists(),
            "closing a pledge left no audit trail",
        )
