"""Direct tests for the catalog stages of the Snipe-IT importer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from core.importers.snipeit.client import SnipeITClient
from core.importers.snipeit.contracts import ImportContext, StageReporter
from core.importers.snipeit.stages.asset_models import AssetModelDependencies, AssetModelImporter
from core.importers.snipeit.stages.catalog import (
    CategoryDependencies,
    CategoryImporter,
    ManufacturerDependencies,
    ManufacturerImporter,
    StatusLabelDependencies,
    StatusLabelImporter,
    SupplierDependencies,
    SupplierImporter,
)
from core.importers.snipeit.stages.custom_fields import (
    CustomFieldDependencies,
    CustomFieldImporter,
    FieldsetDependencies,
    FieldsetImporter,
)
from core.tests.mixins import TenantTestMixin

User = get_user_model()


def _make_client(endpoint: str, rows: list[dict]) -> SnipeITClient:
    client = SnipeITClient.__new__(SnipeITClient)
    client.base_url = "https://snipe.example"
    client.PAGE_SIZE = 500
    client.context = SimpleNamespace(provider="snipe-it", tenant_id=None, actor_id=None, request_id=None)
    client.retry_budget_factory = lambda: SimpleNamespace()

    def fake_get(path, params=None, **kwargs):
        if path == endpoint:
            return {"total": len(rows), "rows": rows}
        return {"total": 0, "rows": []}

    client._get = fake_get
    return client


@pytest.mark.django_db
class TestSnipeITCatalogStages(TenantTestMixin):
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.setup_tenant_context(name="Stage Tenant", slug="stage-tenant")
        self.admin = User.objects.create_superuser(username="stage-admin", email="stage@example.com", password="pw")

    def _context(self, endpoint, rows, *, dry_run=False, update=False):
        return ImportContext(
            client=_make_client(endpoint, rows),
            default_tenant=self.tenant,
            user=self.admin,
            dry_run=dry_run,
            update=update,
            map_companies=False,
            reporter=StageReporter(default_tenant=self.tenant, user=self.admin),
        )

    def _run(self, importer, endpoint, rows, *, dry_run=False, update=False):
        return importer(self._context(endpoint, rows, dry_run=dry_run, update=update)).run()

    def test_status_labels_create_skip_and_update(self):
        from assets.models import StatusLabel

        deps = StatusLabelDependencies({})
        row = {"id": 101, "name": "Stage Ready", "type": "deployable"}
        result = self._run(lambda context: StatusLabelImporter(context, deps), "/api/v1/statuslabels", [row])
        obj = StatusLabel._base_manager.get(name="Stage Ready")
        assert result.counts.as_dict() == {"created": 1, "updated": 0, "skipped": 0, "failed": 0}
        assert deps.status_labels[101] == obj

        skipped = self._run(lambda context: StatusLabelImporter(context, deps), "/api/v1/statuslabels", [row])
        assert skipped.counts.skipped == 1
        assert deps.status_labels[101] == obj

        updated = self._run(
            lambda context: StatusLabelImporter(context, deps),
            "/api/v1/statuslabels",
            [{"id": 101, "name": "Stage Ready", "type": "archived"}],
            update=True,
        )
        obj.refresh_from_db()
        assert updated.counts.updated == 1
        assert obj.type == "archived"

    def test_manufacturers_create_skip_and_update(self):
        from assets.models import Manufacturer

        deps = ManufacturerDependencies({})
        row = {"id": 102, "name": "Stage Acme"}
        created = self._run(lambda context: ManufacturerImporter(context, deps), "/api/v1/manufacturers", [row])
        obj = Manufacturer._base_manager.get(name="Stage Acme")
        assert created.counts.created == 1
        assert deps.manufacturers[102] == obj

        skipped = self._run(lambda context: ManufacturerImporter(context, deps), "/api/v1/manufacturers", [row])
        assert skipped.counts.skipped == 1
        updated = self._run(
            lambda context: ManufacturerImporter(context, deps),
            "/api/v1/manufacturers",
            [row],
            update=True,
        )
        assert updated.counts.updated == 1
        assert deps.manufacturers[102] == obj

    def test_manufacturers_multiple_rows_get_unique_slugs(self):
        """Regression: Manufacturer has no AutoSlugMixin and a partial unique
        index on active slug, so empty slugs collide from the second row on."""
        from assets.models import Manufacturer

        deps = ManufacturerDependencies({})
        rows = [{"id": 201, "name": "Mfr Alpha"}, {"id": 202, "name": "Mfr Beta"}]
        result = self._run(lambda context: ManufacturerImporter(context, deps), "/api/v1/manufacturers", rows)
        assert result.counts.created == 2
        assert result.counts.failed == 0
        slugs = {m.slug for m in Manufacturer._base_manager.filter(name__in=["Mfr Alpha", "Mfr Beta"])}
        assert len(slugs) == 2
        assert all(slugs)
        assert deps.manufacturers[201].slug and deps.manufacturers[202].slug

    def test_categories_create_skip_and_update(self):
        from assets.models import Category

        deps = CategoryDependencies({})
        row = {"id": 103, "name": "Stage Laptops", "category_type": "asset"}
        created = self._run(lambda context: CategoryImporter(context, deps), "/api/v1/categories", [row])
        obj = Category._base_manager.get(name="Stage Laptops")
        assert created.counts.created == 1
        assert obj.applies_to == {"asset": True}
        assert deps.categories[103] == obj

        skipped = self._run(lambda context: CategoryImporter(context, deps), "/api/v1/categories", [row])
        assert skipped.counts.skipped == 1
        updated = self._run(
            lambda context: CategoryImporter(context, deps),
            "/api/v1/categories",
            [{"id": 103, "name": "Stage Laptops", "category_type": "accessory"}],
            update=True,
        )
        obj.refresh_from_db()
        assert updated.counts.updated == 1
        assert obj.applies_to == {"accessory": True}

    def test_suppliers_create_contact_skip_and_update(self):
        from assets.models import Supplier
        from organization.models import Contact, ContactAssignment

        deps = SupplierDependencies({})
        row = {
            "id": 104,
            "name": "Stage Supplier",
            "email": "contact@stage.example",
            "phone": "+49 100",
            "contact": "Stage Contact",
            "url": "https://supplier.example",
            "notes": "Initial notes",
        }
        created = self._run(lambda context: SupplierImporter(context, deps), "/api/v1/suppliers", [row])
        obj = Supplier._base_manager.get(name="Stage Supplier")
        contact = Contact._base_manager.get(name="Stage Contact")
        assert created.counts.created == 1
        assert deps.suppliers[104] == obj
        assert contact.email == "contact@stage.example"
        assert ContactAssignment._base_manager.filter(object_id=obj.pk, priority="primary").count() == 1

        skipped = self._run(lambda context: SupplierImporter(context, deps), "/api/v1/suppliers", [row])
        assert skipped.counts.skipped == 1

        updated = self._run(
            lambda context: SupplierImporter(context, deps),
            "/api/v1/suppliers",
            [{**row, "url": "https://updated.example", "notes": "Updated notes"}],
            update=True,
        )
        obj.refresh_from_db()
        assert updated.counts.updated == 1
        assert obj.website == "https://updated.example"
        assert obj.notes == "Updated notes"

    def test_custom_fields_create_skip_and_update_map_by_db_column(self):
        from extras.models import CustomField

        deps = CustomFieldDependencies({})
        row = {
            "id": 105,
            "name": "Stage CPU",
            "db_column_name": "_snipeit_stage_cpu_105",
            "format": "LIST",
            "field_values": "one\n two",
        }
        created = self._run(lambda context: CustomFieldImporter(context, deps), "/api/v1/fields", [row])
        obj = CustomField._base_manager.get(name="stage_cpu")
        assert created.counts.created == 1
        assert obj.field_type == "select"
        assert obj.choices == "one\ntwo"
        assert deps.custom_fields["_snipeit_stage_cpu_105"] == obj

        skipped = self._run(lambda context: CustomFieldImporter(context, deps), "/api/v1/fields", [row])
        assert skipped.counts.skipped == 1

        updated = self._run(
            lambda context: CustomFieldImporter(context, deps),
            "/api/v1/fields",
            [{**row, "name": "Updated CPU", "format": "TEXT", "field_values": "three"}],
            update=True,
        )
        obj.refresh_from_db()
        assert updated.counts.updated == 1
        assert obj.label == "Updated CPU"
        assert obj.field_type == "text"
        assert obj.choices == "three"

    def test_fieldsets_create_resolves_fields_skip_and_update_does_not_save(self):
        from extras.models import CustomField, CustomFieldset

        field = CustomField.objects.create(name="stage_serial", label="Serial")
        deps = FieldsetDependencies({"_snipeit_stage_serial_106": field}, {})
        row = {
            "id": 106,
            "name": "Stage Specs",
            "fields": {"rows": [{"db_column_name": "_snipeit_stage_serial_106"}]},
        }
        created = self._run(lambda context: FieldsetImporter(context, deps), "/api/v1/fieldsets", [row])
        obj = CustomFieldset._base_manager.get(name="Stage Specs")
        assert created.counts.created == 1
        assert list(obj.fields.all()) == [field]
        assert deps.fieldsets[106] == obj

        skipped = self._run(lambda context: FieldsetImporter(context, deps), "/api/v1/fieldsets", [row])
        assert skipped.counts.skipped == 1

        other = CustomField.objects.create(name="stage_other", label="Other")
        updated = self._run(
            lambda context: FieldsetImporter(context, deps),
            "/api/v1/fieldsets",
            [{**row, "fields": {"rows": [{"db_column_name": "_snipeit_stage_other_106"}]}}],
            update=True,
        )
        obj.refresh_from_db()
        assert updated.counts.updated == 1
        assert list(obj.fields.all()) == [field]
        assert other not in obj.fields.all()

    def test_asset_models_create_skip_update_and_optional_relations(self):
        from assets.models import AssetType, Category, Manufacturer

        manufacturer = Manufacturer.objects.create(name="Stage Model Maker")
        category = Category.objects.create(name="Stage Model Category", applies_to={"asset": True})
        deps = AssetModelDependencies({107: manufacturer}, {108: category}, {}, {})
        row = {
            "id": 107,
            "name": "Stage ThinkPad",
            "manufacturer": {"id": 107},
            "category": {"id": 108},
            "fieldset": None,
            "eol": "36",
            "model_number": "P" * 120,
        }
        created = self._run(lambda context: AssetModelImporter(context, deps), "/api/v1/models", [row])
        obj = AssetType._base_manager.get(model="Stage ThinkPad")
        assert created.counts.created == 1
        assert obj.manufacturer == manufacturer
        assert obj.category == category
        assert obj.custom_fieldset is None
        assert obj.eol_months == 36
        assert len(obj.part_number) == 100
        assert obj.custom_field_data["snipeit_id"] == "107"
        assert deps.asset_models[107] == obj

        skipped = self._run(lambda context: AssetModelImporter(context, deps), "/api/v1/models", [row])
        assert skipped.counts.skipped == 1

        updated = self._run(
            lambda context: AssetModelImporter(context, deps),
            "/api/v1/models",
            [{**row, "name": "Stage ThinkPad Updated", "eol": "48", "model_number": "UPDATED"}],
            update=True,
        )
        obj.refresh_from_db()
        assert updated.counts.updated == 1
        assert obj.model == "Stage ThinkPad Updated"
        assert obj.eol_months == 48
        assert obj.part_number == "UPDATED"

    @pytest.mark.parametrize(
        ("stage", "endpoint", "dependencies", "model_label", "manager_method", "failure_key", "output_name"),
        [
            (
                StatusLabelImporter,
                "/api/v1/statuslabels",
                StatusLabelDependencies({}),
                "StatusLabel",
                "create",
                "name",
                "status_labels",
            ),
            (
                ManufacturerImporter,
                "/api/v1/manufacturers",
                ManufacturerDependencies({}),
                "Manufacturer",
                "get_or_create",
                "name",
                "manufacturers",
            ),
            (
                CategoryImporter,
                "/api/v1/categories",
                CategoryDependencies({}),
                "Category",
                "create",
                "name",
                "categories",
            ),
            (
                SupplierImporter,
                "/api/v1/suppliers",
                SupplierDependencies({}),
                "Supplier",
                "create",
                "name",
                "suppliers",
            ),
            (
                CustomFieldImporter,
                "/api/v1/fields",
                CustomFieldDependencies({}),
                "CustomField",
                "create",
                "name",
                "custom_fields",
            ),
            (
                FieldsetImporter,
                "/api/v1/fieldsets",
                FieldsetDependencies({}, {}),
                "CustomFieldset",
                "create",
                "name",
                "fieldsets",
            ),
        ],
    )
    def test_catalog_row_failure_does_not_publish_failed_map_entry(
        self, monkeypatch, stage, endpoint, dependencies, model_label, manager_method, failure_key, output_name
    ):
        from django.apps import apps

        model = apps.get_model(
            "assets" if model_label in {"StatusLabel", "Manufacturer", "Category", "Supplier"} else "extras",
            model_label,
        )
        manager = model.objects
        original = getattr(manager, manager_method)

        def fail_bad(*args, **kwargs):
            if kwargs.get(failure_key) in {"Broken", "broken_field", "Broken Fieldset"}:
                raise RuntimeError("bad row")
            if (kwargs.get("defaults") or {}).get("name") in {"Broken", "broken_field", "Broken Fieldset"}:
                raise RuntimeError("bad row")
            return original(*args, **kwargs)

        monkeypatch.setattr(manager, manager_method, fail_bad)
        if model_label == "CustomField":
            rows = [
                {"id": 1, "db_column_name": "_snipeit_broken_field_1", "name": "Broken", "format": "TEXT"},
                {"id": 2, "db_column_name": "_snipeit_good_field_2", "name": "Good", "format": "TEXT"},
            ]
        elif model_label == "CustomFieldset":
            rows = [{"id": 1, "name": "Broken Fieldset"}, {"id": 2, "name": "Good Fieldset"}]
        else:
            rows = [{"id": 1, "name": "Broken"}, {"id": 2, "name": "Good"}]
        if model_label == "StatusLabel":
            rows[0]["type"] = rows[1]["type"] = "deployable"
        if model_label == "Category":
            rows[0]["category_type"] = rows[1]["category_type"] = "asset"
        if model_label == "Supplier":
            rows[0].update(email="", phone="", contact="")
            rows[1].update(email="", phone="", contact="")

        deps = dependencies

        def importer(context):
            return stage(context, deps)

        result = self._run(importer, endpoint, rows)
        output = getattr(deps, output_name)
        assert result.counts.failed == 1
        assert result.counts.created == 1
        failed_key = "_snipeit_broken_field_1" if model_label == "CustomField" else 1
        good_key = "_snipeit_good_field_2" if model_label == "CustomField" else 2
        assert failed_key not in output
        assert good_key in output

    def test_asset_model_row_failure_does_not_publish_failed_map_entry(self, monkeypatch):
        from assets.models import AssetType, Manufacturer

        manufacturer = Manufacturer.objects.create(name="Stage Failure Maker")
        deps = AssetModelDependencies({1: manufacturer}, {}, {}, {})
        original = AssetType.objects.create

        def fail_bad(*args, **kwargs):
            if kwargs.get("model") == "Broken Model":
                raise RuntimeError("bad row")
            return original(*args, **kwargs)

        monkeypatch.setattr(AssetType.objects, "create", fail_bad)
        rows = [
            {"id": 1, "name": "Broken Model", "manufacturer": {"id": 1}},
            {"id": 2, "name": "Good Model", "manufacturer": {"id": 1}},
        ]
        result = self._run(lambda context: AssetModelImporter(context, deps), "/api/v1/models", rows)
        assert result.counts.failed == 1
        assert result.counts.created == 1
        assert 1 not in deps.asset_models
        assert 2 in deps.asset_models

    def test_all_seven_stages_dry_run_publish_negative_ids_without_writes(self):
        from assets.models import AssetType, Category, Manufacturer, StatusLabel, Supplier
        from extras.models import CustomField, CustomFieldset

        manufacturer = Manufacturer(id=-1, name="Dry Maker")
        custom_field = CustomField(id=-2, name="dry_field", label="Dry Field", field_type="text")
        stages = [
            (
                StatusLabelImporter,
                StatusLabelDependencies({}),
                "/api/v1/statuslabels",
                [{"id": 201, "name": "Dry Status", "type": "deployable"}],
                201,
            ),
            (
                ManufacturerImporter,
                ManufacturerDependencies({}),
                "/api/v1/manufacturers",
                [{"id": 202, "name": "Dry Maker"}],
                202,
            ),
            (
                CategoryImporter,
                CategoryDependencies({}),
                "/api/v1/categories",
                [{"id": 203, "name": "Dry Category", "category_type": "asset"}],
                203,
            ),
            (
                SupplierImporter,
                SupplierDependencies({}),
                "/api/v1/suppliers",
                [{"id": 204, "name": "Dry Supplier", "email": "", "phone": "", "contact": ""}],
                204,
            ),
            (
                CustomFieldImporter,
                CustomFieldDependencies({}),
                "/api/v1/fields",
                [{"id": 205, "name": "Dry Field", "db_column_name": "_snipeit_dry_field_205", "format": "TEXT"}],
                "_snipeit_dry_field_205",
            ),
            (
                FieldsetImporter,
                FieldsetDependencies({"_snipeit_dry_field_205": custom_field}, {}),
                "/api/v1/fieldsets",
                [{"id": 206, "name": "Dry Fieldset"}],
                206,
            ),
            (
                AssetModelImporter,
                AssetModelDependencies({202: manufacturer}, {}, {}, {}),
                "/api/v1/models",
                [{"id": 207, "name": "Dry Model", "manufacturer": {"id": 202}}],
                207,
            ),
        ]
        before = {
            StatusLabel: StatusLabel._base_manager.count(),
            Manufacturer: Manufacturer._base_manager.count(),
            Category: Category._base_manager.count(),
            Supplier: Supplier._base_manager.count(),
            CustomField: CustomField._base_manager.count(),
            CustomFieldset: CustomFieldset._base_manager.count(),
            AssetType: AssetType._base_manager.count(),
        }
        for stage, dependencies, endpoint, rows, source_id in stages:
            result = self._run(
                lambda context, cls=stage, deps=dependencies: cls(context, deps), endpoint, rows, dry_run=True
            )
            output = (
                dependencies.status_labels
                if isinstance(dependencies, StatusLabelDependencies)
                else dependencies.manufacturers
                if isinstance(dependencies, ManufacturerDependencies)
                else dependencies.categories
                if isinstance(dependencies, CategoryDependencies)
                else dependencies.suppliers
                if isinstance(dependencies, SupplierDependencies)
                else dependencies.custom_fields
                if isinstance(dependencies, CustomFieldDependencies)
                else dependencies.fieldsets
                if isinstance(dependencies, FieldsetDependencies)
                else dependencies.asset_models
            )
            assert result.counts.created == 1
            assert output[source_id].id == -rows[0]["id"]
        assert before[StatusLabel] == StatusLabel._base_manager.count()
        assert before[Manufacturer] == Manufacturer._base_manager.count()
        assert before[Category] == Category._base_manager.count()
        assert before[Supplier] == Supplier._base_manager.count()
        assert before[CustomField] == CustomField._base_manager.count()
        assert before[CustomFieldset] == CustomFieldset._base_manager.count()
        assert before[AssetType] == AssetType._base_manager.count()
