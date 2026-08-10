from __future__ import annotations

import inspect
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.utils.module_loading import import_string

from core.errors import IntegrationContext, IntegrationUnavailableError
from core.importers.snipeit import SnipeITImporter
from core.importers.snipeit.contracts import StageResult
from core.importers.snipeit.stages.catalog import ManufacturerImporter
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin

User = get_user_model()

SNIPE_STATUS_LABELS = [{"id": 1, "name": "Ready to Deploy", "type": "deployable"}]
SNIPE_MANUFACTURERS = [{"id": 1, "name": "Acme Corp"}]
SNIPE_CATEGORIES = [{"id": 1, "name": "Laptops", "category_type": "asset"}]
SNIPE_SUPPLIERS = [{"id": 1, "name": "TechSupply", "email": "", "phone": "", "url": "", "contact": ""}]
SNIPE_COMPANIES = [{"id": 100, "name": "Acme Corp"}]
SNIPE_LOCATIONS = [{"id": 10, "name": "HQ", "parent": None}]
SNIPE_USERS = [
    {
        "id": 5,
        "username": "jdoe",
        "first_name": "John",
        "last_name": "Doe",
        "email": "jdoe@example.com",
        "company": {"id": 100},
    }
]
SNIPE_FIELDS = [
    {
        "id": 3,
        "name": "CPU Model",
        "db_column_name": "_snipeit_cpu_model_3",
        "format": "TEXT",
        "field_values": None,
    }
]
SNIPE_FIELDSETS = [{"id": 2, "name": "Laptop Specs", "fields": {"rows": [{"db_column_name": "_snipeit_cpu_model_3"}]}}]
SNIPE_MODELS = [
    {
        "id": 7,
        "name": "ThinkPad X1",
        "manufacturer": {"id": 1},
        "category": {"id": 1},
        "fieldset": {"id": 2},
        "eol": 36,
        "model_number": "TP-X1",
    }
]
SNIPE_HARDWARE = [
    {
        "id": 42,
        "asset_tag": "NW-0001",
        "serial": "SN-ABC123",
        "name": "Alice Laptop",
        "model": {"id": 7},
        "status_label": {"id": 1},
        "supplier": {"id": 1},
        "location": {"id": 10},
        "rtd_location": None,
        "purchase_date": {"date": "2023-01-15"},
        "purchase_cost": "1299.00",
        "order_number": "PO-2023-0001",
        "notes": "Primary device",
        "warranty_months": 36,
        "company": {"id": 100},
        "assigned_to": None,
        "custom_fields": {},
    }
]

ENDPOINTS = {
    "/api/v1/statuslabels": SNIPE_STATUS_LABELS,
    "/api/v1/manufacturers": SNIPE_MANUFACTURERS,
    "/api/v1/categories": SNIPE_CATEGORIES,
    "/api/v1/suppliers": SNIPE_SUPPLIERS,
    "/api/v1/companies": SNIPE_COMPANIES,
    "/api/v1/locations": SNIPE_LOCATIONS,
    "/api/v1/users": SNIPE_USERS,
    "/api/v1/fields": SNIPE_FIELDS,
    "/api/v1/fieldsets": SNIPE_FIELDSETS,
    "/api/v1/models": SNIPE_MODELS,
    "/api/v1/hardware": SNIPE_HARDWARE,
    "/api/v1/accessories": [],
    "/api/v1/consumables": [],
    "/api/v1/components": [],
    "/api/v1/licenses": [],
    "/api/v1/maintenances": [],
}


class FakeClient:
    def __init__(self, pages=None, failing_endpoint=None):
        self.pages = pages or ENDPOINTS
        self.failing_endpoint = failing_endpoint

    def get_all(self, endpoint):
        if endpoint == self.failing_endpoint:
            raise IntegrationUnavailableError(
                context=IntegrationContext(provider="snipe-it", operation="collection.list"),
                cause_type="Timeout",
            )
        return iter(self.pages.get(endpoint, []))


class FakeJob:
    def __init__(self):
        self.logs = []

    def append_log(self, message):
        self.logs.append(message)


@pytest.mark.django_db
class TestSnipeITStageOrchestration(TenantTestMixin):
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.setup_tenant_context(name="Acme", slug="acme")
        self.admin = User.objects.create_superuser(
            username="orchestrator", email="orchestrator@example.com", password="pw"
        )

    def _importer(self, *, pages=None, map_companies=False, skip=None, stdout=None, job=None, checkout_asset=None):
        kwargs = {
            "client": FakeClient(pages),
            "tenant": self.tenant,
            "user": self.admin,
            "map_companies": map_companies,
            "skip": skip,
            "stdout": stdout,
            "job": job,
            "checkout_inventory_item": MagicMock(),
            "create_component_allocation": MagicMock(),
        }
        if checkout_asset is not None:
            kwargs["checkout_asset"] = checkout_asset
        return SnipeITImporter(**kwargs)

    def _run(self, importer):
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            return importer.run()

    def test_full_run_has_ordered_sixteen_stage_results_and_four_count_keys(self):
        importer = self._importer(map_companies=True)

        counts = self._run(importer)

        expected = [
            "statuslabels",
            "manufacturers",
            "categories",
            "suppliers",
            "companies",
            "locations",
            "users",
            "fields",
            "fieldsets",
            "models",
            "assets",
            "accessories",
            "consumables",
            "components",
            "licenses",
            "maintenances",
        ]
        assert list(counts) == expected
        assert all(set(stats) == {"created", "updated", "skipped", "failed"} for stats in counts.values())
        assert list(importer.stage_results) == expected
        assert all(isinstance(result, StageResult) for result in importer.stage_results.values())

    @pytest.mark.parametrize(
        "entity", ["assets", "accessories", "consumables", "components", "licenses", "maintenances"]
    )
    def test_skip_gate_removes_only_the_requested_stage(self, entity):
        counts = self._run(self._importer(skip={entity}))

        assert entity not in counts

    def test_company_mapping_controls_company_stage(self):
        without_companies = self._run(self._importer())
        assert "companies" not in without_companies

        with_companies = self._run(self._importer(map_companies=True))
        assert "companies" in with_companies

    def test_stop_on_client_error_keeps_only_completed_stage_results(self):
        importer = self._importer()
        importer.client = FakeClient({}, failing_endpoint="/api/v1/models")
        importer.context = importer.context.__class__(
            client=importer.client,
            default_tenant=importer.default_tenant,
            user=importer.user,
            dry_run=importer.dry_run,
            update=importer.update,
            map_companies=importer.map_companies,
            reporter=importer.reporter,
        )

        with pytest.raises(IntegrationUnavailableError):
            self._run(importer)

        assert list(importer.counts) == [
            "statuslabels",
            "manufacturers",
            "categories",
            "suppliers",
            "locations",
            "users",
            "fields",
            "fieldsets",
        ]

    def test_run_rebuilds_maps_after_a_failed_row(self):
        importer = self._importer()
        original_upsert = ManufacturerImporter._upsert
        failed_once = True

        def fail_first(self, model, row):
            nonlocal failed_once
            if failed_once:
                failed_once = False
                raise RuntimeError("rollback")
            return original_upsert(self, model, row)

        with patch.object(ManufacturerImporter, "_upsert", fail_first):
            first = self._run(importer)
        second = self._run(importer)

        assert first["manufacturers"]["failed"] == 1
        assert second["manufacturers"]["created"] == 1

    def test_reporter_writes_stage_start_and_finish_to_stdout_and_job(self):
        stdout = StringIO()
        job = FakeJob()

        self._run(self._importer(stdout=stdout, job=job))

        assert "[statuslabels]" in stdout.getvalue()
        assert "statuslabels:" in stdout.getvalue()
        assert stdout.getvalue() == "".join(job.logs)

    def test_constructor_signature_and_required_inventory_services(self):
        signature = inspect.signature(SnipeITImporter)
        assert "checkout_inventory_item" in signature.parameters
        assert signature.parameters["checkout_inventory_item"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["create_component_allocation"].kind is inspect.Parameter.KEYWORD_ONLY

        with pytest.raises(TypeError):
            SnipeITImporter(client=FakeClient(), tenant=self.tenant, user=self.admin)

    def test_checkout_asset_fallback_and_injected_gateway(self):
        fallback = self._importer()
        assert fallback.checkout_asset is import_string("assets.services.checkout_asset")

        checkout_asset = MagicMock()
        importer = self._importer(checkout_asset=checkout_asset)
        asset = MagicMock(pk=1, tenant_id=self.tenant.pk)
        holder = MagicMock(pk=2, tenant_id=self.tenant.pk)
        status = MagicMock()

        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            importer._hardware_checkout.checkout(
                asset=asset,
                holder=holder,
                status=status,
                tenant_id=self.tenant.pk,
            )

        checkout_asset.assert_called_once_with(
            asset=asset,
            holder=holder,
            user=self.admin,
            status=status,
            notes="Imported from Snipe-IT",
        )
