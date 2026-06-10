from .asset_views import (
    AssetListView, AssetDetailView, AssetEditView, AssetDeleteView,
    AssetCloneView, AssetImportView, AssetBulkEditView, AssetBulkDeleteView,
    AssetCheckoutView, AssetCheckinView, AssetAuditView, asset_label_print,
    bulk_assign_assets, bulk_print_labels,
)
from .assetrole_views import (
    AssetRoleListView, AssetRoleDetailView, AssetRoleEditView, AssetRoleDeleteView,
    AssetRoleCloneView,
)
from .manufacturer_views import (
    ManufacturerListView, ManufacturerDetailView, ManufacturerEditView,
    ManufacturerDeleteView, ManufacturerImportView, ManufacturerCloneView,
)
from .assettype_views import (
    AssetTypeListView, AssetTypeDetailView, AssetTypeEditView, AssetTypeDeleteView,
    AssetTypeCloneView, AssetTypeImportView,
)
from .statuslabel_views import (
    StatusLabelListView, StatusLabelDetailView, StatusLabelEditView, StatusLabelDeleteView,
    StatusLabelCloneView,
)
from .depreciation_views import (
    DepreciationListView, DepreciationDetailView, DepreciationEditView, DepreciationDeleteView,
    DepreciationCloneView,
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
from .audit_views import (
    AuditSessionListView, AuditSessionDetailView, AuditSessionCreateView,
    AssetAuditScanView, AuditSessionCloseView, AuditSessionRehomeView,
    AuditSessionDeleteView,
)
from .maintenance_views import (
    AssetMaintenanceListView, AssetMaintenanceDetailView, AssetMaintenanceEditView,
    AssetMaintenanceCloneView, AssetMaintenanceDeleteView,
)

