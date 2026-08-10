from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from assets.models import Asset, Category, Manufacturer, Supplier
from core.importers.snipeit.client import SnipeITClient
from core.importers.snipeit.common import IMPORT_NOTE, InventoryAssignmentGateway
from core.importers.snipeit.contracts import ImportContext, StageReporter
from core.importers.snipeit.stages.inventory import (
    AccessoryDependencies,
    AccessoryImporter,
    ComponentDependencies,
    ComponentImporter,
    ConsumableDependencies,
    ConsumableImporter,
)
from core.importers.snipeit.stages.maintenances import MaintenanceDependencies, MaintenanceImporter
from core.managers import get_current_tenant
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin
from inventory.models import Accessory, AccessoryAssignment, Component, ComponentAllocation, Consumable
from organization.models import AssetHolder, Location, Site

User = get_user_model()


def _client(responses):
    client = SnipeITClient.__new__(SnipeITClient)
    client.base_url = "https://snipe.example"
    client.PAGE_SIZE = 1
    client.context = SimpleNamespace(provider="snipe-it", operation="test")
    client.retry_budget_factory = lambda: None

    def fake_get(endpoint, params=None, **kwargs):
        offset = (params or {}).get("offset", 0)
        response = responses.get((endpoint, offset), responses.get(endpoint, {"total": 0, "rows": []}))
        if isinstance(response, BaseException):
            raise response
        return response

    client._get = fake_get
    return client


@pytest.mark.django_db
class TestInventoryStages(TenantTestMixin):
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.setup_tenant_context(name="Inventory Stage Tenant", slug="inventory-stage")
        self.admin = self.tenant_admin
        self.set_active_tenant(self.tenant)
        self.manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        self.category = Category.objects.create(name="Accessories", slug="accessories")
        self.supplier = Supplier.objects.create(name="Supply Co", slug="supply-co")
        self.site = Site.objects.create(name="HQ", slug="hq", tenant=self.tenant)
        self.location = Location.objects.create(site=self.site, name="Storage", slug="storage", tenant=self.tenant)
        self.holder = AssetHolder.objects.create(
            user=self.admin,
            first_name="A",
            last_name="Holder",
            upn="holder@example.com",
            email="holder@example.com",
            tenant=self.tenant,
        )
        self.asset = Asset.objects.create(name="Laptop", asset_tag="L-1", tenant=self.tenant)

    def _context(self, responses, *, dry_run=False, update=False):
        reporter = StageReporter(StringIO(), default_tenant=self.tenant, user=self.admin)
        return ImportContext(
            client=_client(responses),
            default_tenant=self.tenant,
            user=self.admin,
            dry_run=dry_run,
            update=update,
            map_companies=False,
            reporter=reporter,
        )

    def _run(self, stage):
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            return stage.run()

    def _inventory_gateway(self):
        checkout = MagicMock()
        allocate = MagicMock()

        def assert_tenant(*args, **kwargs):
            assert get_current_tenant().pk == self.tenant.pk

        checkout.side_effect = assert_tenant
        allocate.side_effect = assert_tenant
        return InventoryAssignmentGateway(checkout, allocate, self.admin), checkout, allocate

    def test_accessory_create_stock_checkedout_pagination_missing_holder_and_idempotency(self):
        gateway, checkout, _ = self._inventory_gateway()
        responses = {
            "/api/v1/accessories": {
                "total": 1,
                "rows": [{"id": 10, "name": "Dock", "qty": 4, "manufacturer": {"id": 1}}],
            },
            ("/api/v1/accessories/10/checkedout", 0): {
                "total": 2,
                "rows": [{"assigned_to": {"id": 1}, "qty": 2}],
            },
            ("/api/v1/accessories/10/checkedout", 1): {
                "total": 2,
                "rows": [{"assigned_to": {"id": 999}, "qty": 3}],
            },
        }
        deps = AccessoryDependencies(
            {1: self.manufacturer},
            {1: self.category},
            {1: self.supplier},
            {},
            {1: self.holder},
            gateway,
        )
        context = self._context(responses)
        stage = AccessoryImporter(context, deps)
        false_result = SimpleNamespace(exists=lambda: False)
        true_result = SimpleNamespace(exists=lambda: True)
        with patch.object(AccessoryAssignment._base_manager, "filter", side_effect=[false_result, true_result]):
            first = self._run(stage)
            second = self._run(stage)

        accessory = Accessory.all_objects.get(custom_field_data__snipeit_id="10")
        assert first.counts.created == 1
        assert second.counts.skipped == 1
        assert accessory.stocks.get().qty == 4
        checkout.assert_called_once_with(accessory, 2, holder=self.holder, user=self.admin, notes=IMPORT_NOTE)

    def test_accessory_checkout_failure_is_warning_and_dry_run_writes_nothing(self):
        gateway, checkout, _ = self._inventory_gateway()
        responses = {
            "/api/v1/accessories": {
                "total": 1,
                "rows": [{"id": 11, "name": "Mouse", "manufacturer": {"id": 1}}],
            },
            "/api/v1/accessories/11/checkedout": RuntimeError("child failure"),
        }
        deps = AccessoryDependencies({1: self.manufacturer}, {}, {}, {}, {1: self.holder}, gateway)
        context = self._context(responses)
        stage = AccessoryImporter(context, deps)
        result = self._run(stage)
        assert result.counts.created == 1
        assert result.warning_count == 1
        assert checkout.call_count == 0

        dry_context = self._context(
            {
                "/api/v1/accessories": {
                    "total": 1,
                    "rows": [{"id": 12, "name": "Keyboard", "manufacturer": {"id": 1}}],
                }
            },
            dry_run=True,
        )
        dry_stage = AccessoryImporter(dry_context, deps)
        dry_result = self._run(dry_stage)
        assert dry_result.counts.created == 1
        assert not Accessory.all_objects.filter(custom_field_data__snipeit_id="12").exists()

    def test_consumable_stock_qty_rule_update_and_dry_run(self):
        rows = {
            "/api/v1/consumables": {
                "total": 2,
                "rows": [
                    {"id": 20, "name": "Cable", "qty": 0, "manufacturer": {"id": 1}},
                    {"id": 21, "name": "Ink", "qty": 3, "manufacturer": {"id": 1}},
                ],
            }
        }
        deps = ConsumableDependencies({1: self.manufacturer}, {1: self.category}, {1: self.supplier}, {})
        result = self._run(ConsumableImporter(self._context(rows), deps))
        assert result.counts.created == 2
        assert not Consumable.all_objects.get(custom_field_data__snipeit_id="20").stocks.exists()
        assert Consumable.all_objects.get(custom_field_data__snipeit_id="21").stocks.get().qty == 3

        update_rows = {
            "/api/v1/consumables": {
                "total": 1,
                "rows": [{"id": 20, "name": "Cable", "notes": "updated", "manufacturer": {"id": 1}}],
            }
        }
        updated = self._run(ConsumableImporter(self._context(update_rows, update=True), deps))
        assert updated.counts.updated == 1
        assert Consumable.all_objects.get(custom_field_data__snipeit_id="20").notes == "updated"

        dry_rows = {
            "/api/v1/consumables": {
                "total": 1,
                "rows": [{"id": 22, "name": "Gloves", "qty": 4, "manufacturer": {"id": 1}}],
            }
        }
        dry = self._run(ConsumableImporter(self._context(dry_rows, dry_run=True), deps))
        assert dry.counts.created == 1
        assert not Consumable.all_objects.filter(custom_field_data__snipeit_id="22").exists()

    def test_component_create_stock_allocations_idempotency_and_warning(self):
        gateway, _, allocate = self._inventory_gateway()
        responses = {
            "/api/v1/components": {
                "total": 1,
                "rows": [{"id": 30, "name": "SSD", "qty": 5, "manufacturer": {"id": 1}}],
            },
            "/api/v1/components/30/assets": {"total": 1, "rows": [{"id": 1, "qty": 2}]},
        }
        deps = ComponentDependencies(
            {1: self.manufacturer}, {1: self.category}, {1: self.supplier}, {}, {1: self.asset}, gateway
        )
        component = ComponentImporter(self._context(responses), deps)
        false_result = SimpleNamespace(exists=lambda: False)
        true_result = SimpleNamespace(exists=lambda: True)
        with patch.object(ComponentAllocation._base_manager, "filter", side_effect=[false_result, true_result]):
            first = self._run(component)
            second = self._run(component)
        obj = Component.all_objects.get(custom_field_data__snipeit_id="30")
        assert first.counts.created == 1
        assert second.counts.skipped == 1
        assert obj.stocks.get().qty == 5
        allocate.assert_called_once_with(obj, 2, asset=self.asset, user=self.admin, notes=IMPORT_NOTE)

        bad_responses = {
            "/api/v1/components": {
                "total": 1,
                "rows": [{"id": 31, "name": "RAM", "qty": 1, "manufacturer": {"id": 1}}],
            },
            "/api/v1/components/31/assets": RuntimeError("allocation failure"),
        }
        bad_client = _client(bad_responses)
        bad_context = self._context(bad_responses)
        bad_context = ImportContext(
            client=bad_client,
            default_tenant=self.tenant,
            user=self.admin,
            dry_run=False,
            update=False,
            map_companies=False,
            reporter=StageReporter(StringIO(), default_tenant=self.tenant, user=self.admin),
        )
        bad = self._run(ComponentImporter(bad_context, deps))
        assert bad.counts.created == 1
        assert bad.warning_count == 1

    def test_maintenances_create_missing_asset_update_and_dry_run_created_count(self):
        rows = {
            "/api/v1/maintenances": {
                "total": 2,
                "rows": [
                    {
                        "id": 40,
                        "asset": {"id": 1},
                        "asset_maintenance_type": "repair",
                        "start_date": {"date": "2024-01-02"},
                        "completion_date": {"date": "2024-01-03"},
                        "cost": "12.50",
                        "notes": "initial",
                    },
                    {"id": 41, "asset": {"id": 999}},
                ],
            }
        }
        deps = MaintenanceDependencies({1: self.asset}, {})
        result = self._run(MaintenanceImporter(self._context(rows), deps))
        assert result.counts.created == 1
        assert result.counts.skipped == 1

        update_rows = {
            "/api/v1/maintenances": {
                "total": 1,
                "rows": [
                    {
                        "id": 40,
                        "asset": {"id": 1},
                        "asset_maintenance_type": "repair",
                        "start_date": "2024-01-02",
                        "notes": "changed",
                    }
                ],
            }
        }
        updated = self._run(MaintenanceImporter(self._context(update_rows, update=True), deps))
        assert updated.counts.updated == 1

        dry = self._run(MaintenanceImporter(self._context(update_rows, dry_run=True), deps))
        assert dry.counts.created == 1
