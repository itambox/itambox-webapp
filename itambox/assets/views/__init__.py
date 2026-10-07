from compliance.views_audit import (
    AssetAuditScanView,
    AuditSessionCloseView,
    AuditSessionCreateView,
    AuditSessionDeleteView,
    AuditSessionDetailView,
    AuditSessionListView,
    AuditSessionRehomeView,
)

from .asset_request_views import (
    AssetRequestCreateView,
    AssetRequestDeleteView,
    AssetRequestDetailView,
    AssetRequestEditView,
    AssetRequestListView,
    AssetRequestQueueView,
)
from .asset_views import (
    AssetAuditView,
    AssetBulkDeleteView,
    AssetBulkEditView,
    AssetCheckinView,
    AssetCheckoutView,
    AssetCloneView,
    AssetDeleteView,
    AssetDetailView,
    AssetEditView,
    AssetListView,
    asset_label_print,
    bulk_print_labels,
)
from .assetrole_views import (
    AssetRoleCloneView,
    AssetRoleDeleteView,
    AssetRoleDetailView,
    AssetRoleEditView,
    AssetRoleListView,
)
from .assettype_views import (
    AssetTypeCloneView,
    AssetTypeDeleteView,
    AssetTypeDetailView,
    AssetTypeEditView,
    AssetTypeListView,
)
from .bulk_scan_views import (
    AssetScanActionResolveView,
    BulkCheckinScanView,
    BulkCheckoutScanView,
    BulkDisposeScanView,
    bulk_checkin_assets,
    bulk_checkout_assets,
    bulk_dispose_assets,
)
from .category_views import (
    CategoryCloneView,
    CategoryDeleteView,
    CategoryDetailView,
    CategoryEditView,
    CategoryListView,
)
from .depreciation_views import (
    DepreciationCloneView,
    DepreciationDeleteView,
    DepreciationDetailView,
    DepreciationEditView,
    DepreciationListView,
)
from .disposal_views import (
    AssetDisposalDeleteView,
    AssetDisposalDetailView,
    AssetDisposalEditView,
    AssetDisposalListView,
    AssetDisposeActionView,
)
from .maintenance_views import (
    AssetMaintenanceCloneView,
    AssetMaintenanceDeleteView,
    AssetMaintenanceDetailView,
    AssetMaintenanceEditView,
    AssetMaintenanceListView,
)
from .manufacturer_views import (
    ManufacturerCloneView,
    ManufacturerDeleteView,
    ManufacturerDetailView,
    ManufacturerEditView,
    ManufacturerListView,
)
from .reservation_views import (
    AssetReservationDeleteView,
    AssetReservationDetailView,
    AssetReservationEditView,
    AssetReservationListView,
)
from .statuslabel_views import (
    StatusLabelCloneView,
    StatusLabelDeleteView,
    StatusLabelDetailView,
    StatusLabelEditView,
    StatusLabelListView,
)
from .supplier_views import (
    SupplierCloneView,
    SupplierDeleteView,
    SupplierDetailView,
    SupplierEditView,
    SupplierListView,
)
from .tag_sequence_views import (
    AssetTagSequenceDeleteView,
    AssetTagSequenceDetailView,
    AssetTagSequenceEditView,
    AssetTagSequenceListView,
)
from .warranty_views import (
    WarrantyDeleteView,
    WarrantyDetailView,
    WarrantyEditView,
    WarrantyListView,
)
