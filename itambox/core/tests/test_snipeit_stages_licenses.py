from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from assets.models import Asset, Manufacturer, Supplier
from core.importers.snipeit.client import SnipeITClient
from core.importers.snipeit.common import IMPORT_NOTE
from core.importers.snipeit.contracts import ImportContext, StageReporter
from core.importers.snipeit.stages.licenses import LicenseDependencies, LicenseImporter
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin
from licenses.models import License, LicenseSeatAssignment
from organization.models import AssetHolder
from software.models import Software

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
class TestLicenseStages(TenantTestMixin):
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.setup_tenant_context(name="License Stage Tenant", slug="license-stage")
        self.admin = self.tenant_admin
        self.set_active_tenant(self.tenant)
        self.manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        self.supplier = Supplier.objects.create(name="Supply Co", slug="supply-co")
        self.holder = AssetHolder.objects.create(
            user=self.admin,
            first_name="A",
            last_name="Holder",
            upn="license-holder@example.com",
            email="license-holder@example.com",
            tenant=self.tenant,
        )
        self.asset = Asset.objects.create(name="License Laptop", asset_tag="LL-1", tenant=self.tenant)

    def _context(self, responses, *, dry_run=False, update=False):
        return ImportContext(
            client=_client(responses),
            default_tenant=self.tenant,
            user=self.admin,
            dry_run=dry_run,
            update=update,
            map_companies=False,
            reporter=StageReporter(StringIO(), default_tenant=self.tenant, user=self.admin),
        )

    def _run(self, stage):
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            return stage.run()

    def _dependencies(self):
        return LicenseDependencies({1: self.manufacturer}, {1: self.supplier}, {}, {1: self.holder}, {1: self.asset})

    def test_private_software_cache_is_keyed_by_license_id(self):
        row = {
            "id": 1,
            "name": "Office entitlement",
            "product_name": "Office",
            "manufacturer": {"id": 1},
            "supplier": {"id": 1},
            "seats": 2,
        }
        responses = {"/api/v1/licenses": {"total": 2, "rows": [row, row.copy()]}}
        stage = LicenseImporter(self._context(responses), self._dependencies())
        result = self._run(stage)
        assert result.counts.created == 1
        assert result.counts.skipped == 1
        assert Software.all_objects.filter(name="Office").count() == 1
        assert stage._software_cache.keys() == {1}

    def test_license_create_skip_and_exact_update_fields_with_custom_data_merge(self):
        row = {
            "id": 2,
            "name": "Editor",
            "product_name": "Editor Suite",
            "manufacturer": {"id": 1},
            "supplier": {"id": 1},
            "seats": 4,
            "serial": "secret-key",
            "purchase_date": {"date": "2024-01-02"},
            "expiration_date": {"date": "2025-01-02"},
            "purchase_cost": "25.50",
            "order_number": "PO-2",
            "notes": "created",
        }
        responses = {"/api/v1/licenses": {"total": 1, "rows": [row]}}
        deps = self._dependencies()
        created = self._run(LicenseImporter(self._context(responses), deps))
        license_obj = License.all_objects.get(custom_field_data__snipeit_id="2")
        assert created.counts.created == 1
        assert license_obj.license_type == "subscription_seat"
        assert license_obj.supplier == self.supplier

        skipped = self._run(LicenseImporter(self._context(responses), deps))
        assert skipped.counts.skipped == 1

        license_obj.custom_field_data = {"keep": "yes", "snipeit_id": "2"}
        license_obj.save()
        update_row = {
            **row,
            "seats": 7,
            "serial": "new-key",
            "expiration_date": None,
            "notes": "updated",
        }
        updated = self._run(
            LicenseImporter(self._context({"/api/v1/licenses": {"total": 1, "rows": [update_row]}}, update=True), deps)
        )
        license_obj.refresh_from_db()
        assert updated.counts.updated == 1
        assert license_obj.seats == 7
        assert license_obj.license_type == "perpetual_seat"
        assert license_obj.notes == "updated"
        assert license_obj.custom_field_data == {"keep": "yes", "snipeit_id": "2"}

    def test_license_seats_assign_holder_and_asset_and_are_idempotent(self):
        row = {
            "id": 3,
            "name": "Design seats",
            "product_name": "Design Tool",
            "manufacturer": {"id": 1},
            "seats": 2,
        }
        responses = {
            "/api/v1/licenses": {"total": 1, "rows": [row]},
            "/api/v1/licenses/3/seats": {
                "total": 2,
                "rows": [
                    {"assigned_user": {"id": 1}},
                    {"assigned_asset": {"id": 1}},
                ],
            },
        }
        deps = self._dependencies()
        first = self._run(LicenseImporter(self._context(responses), deps))
        second = self._run(LicenseImporter(self._context(responses), deps))
        license_obj = License.all_objects.get(custom_field_data__snipeit_id="3")
        seats = LicenseSeatAssignment.all_objects.filter(license=license_obj, deleted_at__isnull=True)
        assert first.counts.created == 1
        assert second.counts.skipped == 1
        assert seats.count() == 2
        assert seats.filter(assigned_holder=self.holder, notes=IMPORT_NOTE).exists()
        assert seats.filter(asset=self.asset, notes=IMPORT_NOTE).exists()

    def test_seat_failure_is_warning_and_dry_run_has_no_writes(self):
        row = {"id": 4, "name": "Broken seats", "product_name": "Broken Tool", "manufacturer": {"id": 1}}
        bad_responses = {
            "/api/v1/licenses": {"total": 1, "rows": [row]},
            "/api/v1/licenses/4/seats": RuntimeError("seat failure"),
        }
        result = self._run(LicenseImporter(self._context(bad_responses), self._dependencies()))
        assert result.counts.created == 1
        assert result.warning_count == 1

        dry_row = {"id": 5, "name": "Dry license", "product_name": "Dry Tool", "manufacturer": {"id": 1}}
        dry = self._run(
            LicenseImporter(
                self._context({"/api/v1/licenses": {"total": 1, "rows": [dry_row]}}, dry_run=True),
                self._dependencies(),
            )
        )
        assert dry.counts.created == 1
        assert not License.all_objects.filter(custom_field_data__snipeit_id="5").exists()
        assert not Software.all_objects.filter(name="Dry Tool").exists()
