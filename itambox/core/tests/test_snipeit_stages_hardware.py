from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

from assets.choices import StatusTypeChoices
from assets.models import Asset, AssetAssignment, AssetType, Category, Manufacturer, StatusLabel, Supplier, Warranty
from assets.services import checkout_asset as checkout_asset_service
from core.importers.snipeit.common import HardwareCheckoutGateway
from core.importers.snipeit.contracts import ImportContext, StageReporter
from core.importers.snipeit.stages.hardware import HardwareDependencies, HardwareImporter
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField
from organization.models import AssetHolder, Location, Site

User = get_user_model()


class Client:
    def __init__(self, rows):
        self.rows = rows

    def get_all(self, endpoint):
        assert endpoint == "/api/v1/hardware"
        return iter(self.rows)


@pytest.mark.django_db
class TestHardwareImporter(TenantTestMixin):
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.setup_tenant_context(name="Acme", slug="acme")
        self.admin = User.objects.create_superuser(username="impadmin", email="impadmin@example.com", password="pw")
        self.manufacturer = Manufacturer.objects.create(name="Acme Devices", slug="acme-devices")
        self.category = Category.objects.create(name="Laptops", slug="laptops", applies_to={"asset": True})
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad X1",
            slug="acme-devices-thinkpad-x1",
            category=self.category,
        )
        self.status = StatusLabel.objects.create(
            name="Ready",
            slug="ready",
            type=StatusTypeChoices.DEPLOYABLE,
            color="00ff00",
        )
        self.supplier = Supplier.objects.create(name="Tech Supply", slug="tech-supply")
        self.site = Site.objects.create(name="HQ", slug="hq", tenant=self.tenant)
        self.location = Location.objects.create(name="Floor 1", slug="floor-1", site=self.site, tenant=self.tenant)
        self.holder = AssetHolder.objects.create(
            user=self.admin,
            first_name="Jane",
            last_name="Doe",
            upn="jane.doe",
            email="jane@example.com",
            tenant=self.tenant,
        )
        self.custom_field = CustomField.objects.create(name="cpu_model", label="CPU Model")

    def _row(self, source_id=42, **overrides):
        row = {
            "id": source_id,
            "asset_tag": "NW-0001",
            "serial": "SN-ABC123",
            "name": "Imported Laptop",
            "model": {"id": 7},
            "status_label": {"id": 1},
            "supplier": {"id": 1},
            "location": {"id": 10},
            "rtd_location": None,
            "purchase_date": {"date": "2023-01-15"},
            "purchase_cost": "1299.00",
            "order_number": "PO-2023-0001",
            "notes": "Imported notes",
            "warranty_months": 36,
            "company": None,
            "assigned_to": None,
            "custom_fields": {
                "CPU Model": {
                    "field": "_snipeit_cpu_model_3",
                    "value": "Intel i7",
                }
            },
        }
        row.update(overrides)
        return row

    def _dependencies(self, checkout_asset=None):
        checkout_asset = checkout_asset or MagicMock(side_effect=checkout_asset_service)
        dependencies = HardwareDependencies(
            status_labels={1: self.status},
            asset_models={7: self.asset_type},
            tenants={},
            suppliers={1: self.supplier},
            locations={10: self.location},
            custom_fields={"_snipeit_cpu_model_3": self.custom_field},
            holders={5: self.holder},
            assets={},
            checkout=HardwareCheckoutGateway(checkout_asset, self.admin),
        )
        return dependencies, checkout_asset

    def _run(self, rows, *, update=False, dry_run=False, checkout_asset=None, dependencies=None):
        if dependencies is None:
            dependencies, checkout_asset = self._dependencies(checkout_asset)
        elif checkout_asset is None:
            checkout_asset = dependencies.checkout._checkout_asset
        reporter = StageReporter(default_tenant=self.tenant, user=self.admin)
        context = ImportContext(
            client=Client(rows),
            default_tenant=self.tenant,
            user=self.admin,
            dry_run=dry_run,
            update=update,
            map_companies=False,
            reporter=reporter,
        )
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            result = HardwareImporter(context, dependencies).run()
        return result, dependencies, checkout_asset

    def test_persistence_create_warranty_custom_fields_and_map(self):
        result, dependencies, _ = self._run([self._row()])

        asset = Asset._base_manager.get(asset_tag="NW-0001")
        warranty = Warranty._base_manager.get(asset=asset)
        assert result.counts.created == 1
        assert dependencies.assets[42] == asset
        assert asset.asset_type == self.asset_type
        assert asset.purchase_cost == Decimal("1299.00")
        assert asset.custom_field_data == {"snipeit_id": "42", "cpu_model": "Intel i7"}
        assert warranty.start_date.isoformat() == "2023-01-15"
        assert warranty.end_date.isoformat() == "2026-01-15"
        assert warranty.provider == self.supplier.name

    def test_match_order_snipeit_id_wins_over_serial_and_asset_tag(self):
        by_id = Asset.objects.create(
            name="By source ID",
            asset_tag="ID-TAG",
            serial_number="ID-SERIAL",
            tenant=self.tenant,
            custom_field_data={"snipeit_id": "42"},
        )
        by_serial = Asset.objects.create(
            name="By serial",
            asset_tag="SERIAL-TAG",
            serial_number="SN-ABC123",
            tenant=self.tenant,
        )
        by_tag = Asset.objects.create(
            name="By tag",
            asset_tag="NW-0001",
            serial_number="TAG-SERIAL",
            tenant=self.tenant,
        )

        result, dependencies, _ = self._run([self._row()])

        by_id.refresh_from_db()
        by_serial.refresh_from_db()
        by_tag.refresh_from_db()
        assert result.counts.skipped == 1
        assert dependencies.assets[42] == by_id
        assert by_id.name == "By source ID"
        assert by_id.pk != by_serial.pk
        assert by_serial.name == "By serial"
        assert by_tag.name == "By tag"

    def test_update_refreshes_fields_and_warranty(self):
        self._run([self._row(source_id=8)])
        updated = self._row(
            source_id=8,
            name="Updated Laptop",
            serial="SN-UPDATED",
            purchase_date={"date": "2024-02-01"},
            purchase_cost="999.50",
            warranty_months=12,
            notes="Updated notes",
            supplier=None,
        )

        result, _, _ = self._run([updated], update=True)

        asset = Asset._base_manager.get(custom_field_data__snipeit_id="8")
        warranty = Warranty._base_manager.get(asset=asset)
        assert result.counts.updated == 1
        assert asset.name == "Updated Laptop"
        assert asset.serial_number == "SN-UPDATED"
        assert asset.purchase_cost == Decimal("999.50")
        assert asset.notes == "Updated notes"
        assert warranty.end_date.isoformat() == "2025-02-01"
        assert warranty.provider == ""

    def test_rerun_is_idempotent_for_persistence_and_warranty(self):
        row = self._row(source_id=9)
        first, _, _ = self._run([row])
        second, _, _ = self._run([row])

        asset = Asset._base_manager.get(custom_field_data__snipeit_id="9")
        assert first.counts.created == 1
        assert second.counts.skipped == 1
        assert Asset._base_manager.filter(custom_field_data__snipeit_id="9").count() == 1
        assert Warranty._base_manager.filter(asset=asset).count() == 1

    @pytest.mark.parametrize("target_type", ["user", "location", "asset"])
    def test_checkout_targets_create_assignment_and_apply_deployed_status(self, target_type):
        if target_type == "asset":
            rows = [self._row(source_id=5, asset_tag="TARGET", serial="TARGET-SERIAL"), self._row(source_id=6)]
            rows[1]["assigned_to"] = {"type": "asset", "id": 5}
            dependencies, checkout_asset = self._dependencies()
            result, _, _ = self._run(rows, dependencies=dependencies, checkout_asset=checkout_asset)
            source = Asset._base_manager.get(custom_field_data__snipeit_id="6")
            target = Asset._base_manager.get(custom_field_data__snipeit_id="5")
            assert source.active_assignment.assigned_asset_id == target.pk
        else:
            target_id = 5 if target_type == "user" else 10
            row = self._row(source_id=6, assigned_to={"type": target_type, "id": target_id})
            result, _, checkout_asset = self._run([row])
            source = Asset._base_manager.get(custom_field_data__snipeit_id="6")
            assert source.active_assignment is not None
            if target_type == "user":
                assert source.active_assignment.assigned_user_id == self.holder.pk
            else:
                assert source.active_assignment.assigned_location_id == self.location.pk

        assert result.counts.failed == 0
        assert source.status.type == StatusTypeChoices.DEPLOYED
        assert checkout_asset.call_count == 1

    def test_exact_target_rerun_does_not_call_gateway_or_create_history(self):
        checkout_asset = MagicMock(side_effect=checkout_asset_service)
        row = self._row(source_id=11, assigned_to={"type": "user", "id": 5})

        self._run([row], checkout_asset=checkout_asset)
        second, _, _ = self._run([row], checkout_asset=checkout_asset)

        asset = Asset._base_manager.get(custom_field_data__snipeit_id="11")
        assert second.counts.failed == 0
        assert checkout_asset.call_count == 1
        assert AssetAssignment._base_manager.filter(asset=asset, deleted_at__isnull=True).count() == 1

    def test_checkout_missing_target_is_ignored(self):
        checkout_asset = MagicMock(side_effect=checkout_asset_service)
        row = self._row(source_id=12, assigned_to={"type": "user", "id": 999})

        result, _, _ = self._run([row], checkout_asset=checkout_asset)

        assert result.counts.failed == 0
        assert result.warning_count == 0
        assert checkout_asset.call_count == 0

    def test_checkout_failure_is_warning_without_row_failure(self):
        checkout_asset = MagicMock(side_effect=RuntimeError("checkout failed"))
        row = self._row(source_id=13, assigned_to={"type": "user", "id": 5})

        result, _, _ = self._run([row], checkout_asset=checkout_asset)

        assert result.counts.created == 1
        assert result.counts.failed == 0
        assert result.warning_count == 1
        assert Asset._base_manager.filter(custom_field_data__snipeit_id="13").exists()

    def test_row_failure_does_not_publish_map_entry(self):
        row = self._row(source_id=14, asset_tag="x" * 51)

        result, dependencies, _ = self._run([row])

        assert result.counts.failed == 1
        assert 14 not in dependencies.assets
        assert not Asset._base_manager.filter(custom_field_data__snipeit_id="14").exists()

    def test_dry_run_uses_negative_ids_and_does_not_checkout_or_write(self):
        checkout_asset = MagicMock(side_effect=checkout_asset_service)
        row = self._row(source_id=15, assigned_to={"type": "user", "id": 5})

        result, dependencies, _ = self._run([row], dry_run=True, checkout_asset=checkout_asset)

        assert result.counts.created == 1
        assert dependencies.assets[15].pk == -15
        assert Asset._base_manager.count() == 0
        assert Warranty._base_manager.count() == 0
        assert StatusLabel._base_manager.filter(name="Deployed (imported)").count() == 0
        assert checkout_asset.call_count == 0
