from .dashboard import DashboardView
from .asset_views import (
    AssetListView, AssetDetailView, AssetEditView, AssetDeleteView,
    AssetCloneView, AssetImportView, AssetBulkEditView, AssetBulkDeleteView,
    AssetCheckoutView, AssetCheckinView, asset_audit, asset_label_print,
    bulk_assign_assets,
)
from .assetrole_views import (
    AssetRoleListView, AssetRoleDetailView, AssetRoleEditView, AssetRoleDeleteView,
)
from .manufacturer_views import (
    ManufacturerListView, ManufacturerDetailView, ManufacturerEditView,
    ManufacturerDeleteView, ManufacturerImportView,
)
from .assettype_views import (
    AssetTypeListView, AssetTypeDetailView, AssetTypeEditView, AssetTypeDeleteView,
    AssetTypeCloneView, AssetTypeImportView,
)
from .statuslabel_views import (
    StatusLabelListView, StatusLabelDetailView, StatusLabelEditView, StatusLabelDeleteView,
)
from .depreciation_views import (
    DepreciationListView, DepreciationDetailView, DepreciationEditView, DepreciationDeleteView,
)
from .supplier_views import (
    SupplierListView, SupplierDetailView, SupplierEditView, SupplierDeleteView, SupplierCloneView,
)
from .category_views import (
    CategoryListView, CategoryDetailView, CategoryEditView, CategoryDeleteView, CategoryCloneView,
)
from .asset_request_views import (
    AssetRequestListView, AssetRequestDetailView, AssetRequestCreateView,
    AssetRequestEditView, AssetRequestQueueView, AssetRequestDeleteView,
)
from .tag_sequence_views import (
    AssetTagSequenceListView, AssetTagSequenceDetailView,
    AssetTagSequenceEditView, AssetTagSequenceDeleteView,
)
