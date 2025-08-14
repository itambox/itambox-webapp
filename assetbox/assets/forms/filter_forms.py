from core.forms import FilterForm
from ..filters import (
    AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet,
    ComponentTypeFilterSet, ComponentInstanceFilterSet, AccessoryFilterSet,
    ConsumableFilterSet, StatusLabelFilterSet, AssetMaintenanceFilterSet,
    CustomFieldFilterSet, CustomFieldsetFilterSet, DepreciationFilterSet, KitFilterSet,
    SupplierFilterSet, CategoryFilterSet, AssetRequestFilterSet, AssetTagSequenceFilterSet
)

class AssetFilterForm(FilterForm):
    filterset_class = AssetFilterSet

class AssetRoleFilterForm(FilterForm):
    filterset_class = AssetRoleFilterSet

class ManufacturerFilterForm(FilterForm):
    filterset_class = ManufacturerFilterSet

class AssetTypeFilterForm(FilterForm):
    filterset_class = AssetTypeFilterSet

class ComponentTypeFilterForm(FilterForm):
    filterset_class = ComponentTypeFilterSet

class ComponentInstanceFilterForm(FilterForm):
    filterset_class = ComponentInstanceFilterSet

class AccessoryFilterForm(FilterForm):
    filterset_class = AccessoryFilterSet

class ConsumableFilterForm(FilterForm):
    filterset_class = ConsumableFilterSet

class StatusLabelFilterForm(FilterForm):
    filterset_class = StatusLabelFilterSet

class AssetMaintenanceFilterForm(FilterForm):
    filterset_class = AssetMaintenanceFilterSet

class CustomFieldFilterForm(FilterForm):
    filterset_class = CustomFieldFilterSet

class CustomFieldsetFilterForm(FilterForm):
    filterset_class = CustomFieldsetFilterSet

class DepreciationFilterForm(FilterForm):
    filterset_class = DepreciationFilterSet

class KitFilterForm(FilterForm):
    filterset_class = KitFilterSet

class SupplierFilterForm(FilterForm):
    filterset_class = SupplierFilterSet

class CategoryFilterForm(FilterForm):
    filterset_class = CategoryFilterSet

class AssetRequestFilterForm(FilterForm):
    filterset_class = AssetRequestFilterSet

class AssetTagSequenceFilterForm(FilterForm):
    filterset_class = AssetTagSequenceFilterSet
