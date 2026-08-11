from __future__ import annotations

from types import MappingProxyType

from django.utils.module_loading import import_string

from .common import HardwareCheckoutGateway, InventoryAssignmentGateway
from .contracts import ImportContext, ImportStage, StageReporter, StageResult
from .stages.asset_models import AssetModelDependencies, AssetModelImporter
from .stages.catalog import (
    CategoryDependencies,
    CategoryImporter,
    ManufacturerDependencies,
    ManufacturerImporter,
    StatusLabelDependencies,
    StatusLabelImporter,
    SupplierDependencies,
    SupplierImporter,
)
from .stages.custom_fields import (
    CustomFieldDependencies,
    CustomFieldImporter,
    FieldsetDependencies,
    FieldsetImporter,
)
from .stages.hardware import HardwareDependencies, HardwareImporter
from .stages.inventory import (
    AccessoryDependencies,
    AccessoryImporter,
    ComponentDependencies,
    ComponentImporter,
    ConsumableDependencies,
    ConsumableImporter,
)
from .stages.licenses import LicenseDependencies, LicenseImporter
from .stages.maintenances import MaintenanceDependencies, MaintenanceImporter
from .stages.organization import (
    CompanyDependencies,
    CompanyImporter,
    LocationDependencies,
    LocationImporter,
    UserDependencies,
    UserImporter,
)


class SnipeITImporter:
    def __init__(
        self,
        client,
        tenant,
        user,
        dry_run=False,
        update=False,
        map_companies=False,
        skip=None,
        job=None,
        stdout=None,
        *,
        checkout_inventory_item,
        create_component_allocation,
        checkout_asset=None,
        deployed_status_type=None,
        warranty_type=None,
    ):
        self.client = client
        self.default_tenant = tenant
        self.user = user
        self.dry_run = dry_run
        self.update = update
        self.map_companies = map_companies
        self.skip = set(skip or ())
        self.job = job
        self.stdout = stdout
        self.checkout_asset = (
            checkout_asset if checkout_asset is not None else import_string("assets.services.checkout_asset")
        )
        self.deployed_status_type = deployed_status_type
        self.warranty_type = warranty_type

        self.reporter = StageReporter(stdout, job, default_tenant=tenant, user=user)
        self.context = ImportContext(
            client=client,
            default_tenant=tenant,
            user=user,
            dry_run=dry_run,
            update=update,
            map_companies=map_companies,
            reporter=self.reporter,
        )
        self._inventory_assignments = InventoryAssignmentGateway(
            checkout_inventory_item,
            create_component_allocation,
            user,
        )
        self._hardware_checkout = HardwareCheckoutGateway(self.checkout_asset, user)
        self.stage_results: dict[str, StageResult] = {}
        self.counts: dict[str, dict] = {}

    @staticmethod
    def _read(maps: dict[str, dict], name: str):
        return MappingProxyType(maps[name])

    def _run_stage(self, stage: ImportStage) -> None:
        result = stage.run()
        self.stage_results[result.key] = result
        self.counts[result.key] = result.counts.as_dict()

    def run(self) -> dict[str, dict]:
        maps = {
            "status_labels": {},
            "manufacturers": {},
            "categories": {},
            "suppliers": {},
            "tenants": {},
            "locations": {},
            "holders": {},
            "custom_fields": {},
            "fieldsets": {},
            "asset_models": {},
            "assets": {},
        }
        self._maps = maps
        self.stage_results = {}
        self.counts = {}

        self._run_stage(StatusLabelImporter(self.context, StatusLabelDependencies(maps["status_labels"])))
        self._run_stage(ManufacturerImporter(self.context, ManufacturerDependencies(maps["manufacturers"])))
        self._run_stage(CategoryImporter(self.context, CategoryDependencies(maps["categories"])))
        self._run_stage(SupplierImporter(self.context, SupplierDependencies(maps["suppliers"])))
        if self.map_companies:
            self._run_stage(CompanyImporter(self.context, CompanyDependencies(maps["tenants"])))
        self._run_stage(
            LocationImporter(
                self.context,
                LocationDependencies(self._read(maps, "tenants"), maps["locations"]),
            )
        )
        self._run_stage(
            UserImporter(
                self.context,
                UserDependencies(self._read(maps, "tenants"), maps["holders"]),
            )
        )
        self._run_stage(CustomFieldImporter(self.context, CustomFieldDependencies(maps["custom_fields"])))
        self._run_stage(
            FieldsetImporter(
                self.context,
                FieldsetDependencies(self._read(maps, "custom_fields"), maps["fieldsets"]),
            )
        )
        self._run_stage(
            AssetModelImporter(
                self.context,
                AssetModelDependencies(
                    self._read(maps, "manufacturers"),
                    self._read(maps, "categories"),
                    self._read(maps, "fieldsets"),
                    maps["asset_models"],
                ),
            )
        )
        if "assets" not in self.skip:
            self._run_stage(
                HardwareImporter(
                    self.context,
                    HardwareDependencies(
                        self._read(maps, "status_labels"),
                        self._read(maps, "asset_models"),
                        self._read(maps, "tenants"),
                        self._read(maps, "suppliers"),
                        self._read(maps, "locations"),
                        self._read(maps, "custom_fields"),
                        self._read(maps, "holders"),
                        maps["assets"],
                        self._hardware_checkout,
                        deployed_type=self.deployed_status_type or "deployed",
                        warranty_type=self.warranty_type or "hardware",
                    ),
                )
            )
        if "accessories" not in self.skip:
            self._run_stage(
                AccessoryImporter(
                    self.context,
                    AccessoryDependencies(
                        self._read(maps, "manufacturers"),
                        self._read(maps, "categories"),
                        self._read(maps, "suppliers"),
                        self._read(maps, "tenants"),
                        self._read(maps, "holders"),
                        self._inventory_assignments,
                    ),
                )
            )
        if "consumables" not in self.skip:
            self._run_stage(
                ConsumableImporter(
                    self.context,
                    ConsumableDependencies(
                        self._read(maps, "manufacturers"),
                        self._read(maps, "categories"),
                        self._read(maps, "suppliers"),
                        self._read(maps, "tenants"),
                    ),
                )
            )
        if "components" not in self.skip:
            self._run_stage(
                ComponentImporter(
                    self.context,
                    ComponentDependencies(
                        self._read(maps, "manufacturers"),
                        self._read(maps, "categories"),
                        self._read(maps, "suppliers"),
                        self._read(maps, "tenants"),
                        self._read(maps, "assets"),
                        self._inventory_assignments,
                    ),
                )
            )
        if "licenses" not in self.skip:
            self._run_stage(
                LicenseImporter(
                    self.context,
                    LicenseDependencies(
                        self._read(maps, "manufacturers"),
                        self._read(maps, "suppliers"),
                        self._read(maps, "tenants"),
                        self._read(maps, "holders"),
                        self._read(maps, "assets"),
                    ),
                )
            )
        if "maintenances" not in self.skip:
            self._run_stage(
                MaintenanceImporter(
                    self.context,
                    MaintenanceDependencies(self._read(maps, "assets"), self._read(maps, "suppliers")),
                )
            )
        return self.counts
