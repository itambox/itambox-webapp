from core.forms import FilterForm
from ..filters import (
    AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet,
    StatusLabelFilterSet, DepreciationFilterSet,
    SupplierFilterSet, CategoryFilterSet, AssetRequestFilterSet, AssetTagSequenceFilterSet,
    AuditSessionFilterSet
)

class AssetFilterForm(FilterForm):
    filterset_class = AssetFilterSet
    ajax_fields = {
        'location': {
            'url_name': 'api:organization_api:location-list',
            'value_field': 'id',
            'label_field': 'name',
        },
        'asset_type': {
            'url_name': 'api:assets_api:assettype-list',
            'value_field': 'id',
            'label_field': 'model',
        },
        'manufacturer': {
            'url_name': 'api:assets_api:manufacturer-list',
            'value_field': 'id',
            'label_field': 'name',
        },
        'supplier': {
            'url_name': 'api:assets_api:supplier-list',
            'value_field': 'id',
            'label_field': 'name',
        },
        'tenant': {
            'url_name': 'api:organization_api:tenant-list',
            'value_field': 'id',
            'label_field': 'name',
        },
        'tags': {
            'url_name': 'api:extras_api:tag-list',
            'value_field': 'slug',
            'label_field': 'name',
        },
     }

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

class AuditSessionFilterForm(FilterForm):
    filterset_class = AuditSessionFilterSet

