from core.forms import FilterForm
from ..filters import (
    AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet,
    StatusLabelFilterSet, DepreciationFilterSet,
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

class StatusLabelFilterForm(FilterForm):
    filterset_class = StatusLabelFilterSet

class DepreciationFilterForm(FilterForm):
    filterset_class = DepreciationFilterSet

class SupplierFilterForm(FilterForm):
    filterset_class = SupplierFilterSet

class CategoryFilterForm(FilterForm):
    filterset_class = CategoryFilterSet

class AssetRequestFilterForm(FilterForm):
    filterset_class = AssetRequestFilterSet

class AssetTagSequenceFilterForm(FilterForm):
    filterset_class = AssetTagSequenceFilterSet
