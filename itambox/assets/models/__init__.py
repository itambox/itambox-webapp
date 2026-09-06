"""assets.models package — re-exports every public name that was importable from
the old flat assets/models.py module so all ~142 consumer sites keep working
unchanged (import-preserving refactor).

Import order: choices → catalog → asset → tagsequence → assignment → requests
              → lifecycle → maintenance.

Django registers models by app_label (inferred from the package path
``itambox/assets/models/``) — no explicit ``class Meta: app_label`` needed.
Migrations reference ``'assets.ModelName'`` which is unaffected.
"""

# This re-export order follows the documented model dependency chain above;
# preserve it so future model imports cannot introduce an initialization cycle.
# isort: off

# ── 1. Lifecycle-local choice enums ─────────────────────────────────────────
from assets.models.choices import (
    DisposalMethodChoices,
    DataSanitizationMethodChoices,
    WarrantyTypeChoices,
    ReservationStatusChoices,
    MaintenanceStatusChoices,
)

# ── 2. Catalog (reference data) ──────────────────────────────────────────────
from assets.models.catalog import (
    StatusLabel,
    AssetRole,
    Manufacturer,
    Depreciation,
    AssetType,
    AssetTypeFieldset,
    Supplier,
    Category,
    CategoryDefaultFieldset,
)

# ── 3. Core asset + state machine ────────────────────────────────────────────
from assets.models.asset import (
    AssetStateMachine,
    Asset,
)

# ── 4. Tag sequencing ────────────────────────────────────────────────────────
from assets.models.tagsequence import AssetTagSequence

# ── 5. Assignment ────────────────────────────────────────────────────────────
from assets.models.assignment import AssetAssignment

# ── 6. Requests ──────────────────────────────────────────────────────────────
from assets.models.requests import AssetRequest

# ── 7. Lifecycle (disposal / warranty / reservation) ────────────────────────
from assets.models.lifecycle import (
    DateRange,
    AssetDisposal,
    Warranty,
    AssetReservation,
)

# ── 8. Maintenance ───────────────────────────────────────────────────────────
from assets.models.maintenance import AssetMaintenance

# isort: on

__all__ = [
    # choices
    "DisposalMethodChoices",
    "DataSanitizationMethodChoices",
    "WarrantyTypeChoices",
    "ReservationStatusChoices",
    "MaintenanceStatusChoices",
    # catalog
    "StatusLabel",
    "AssetRole",
    "Manufacturer",
    "Depreciation",
    "AssetType",
    "AssetTypeFieldset",
    "Supplier",
    "Category",
    "CategoryDefaultFieldset",
    # asset
    "AssetStateMachine",
    "Asset",
    # tag sequence
    "AssetTagSequence",
    # assignment
    "AssetAssignment",
    # requests
    "AssetRequest",
    # lifecycle
    "DateRange",
    "AssetDisposal",
    "Warranty",
    "AssetReservation",
    # maintenance
    "AssetMaintenance",
]
