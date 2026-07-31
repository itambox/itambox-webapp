import warnings as py_warnings

from django.apps import AppConfig
from django.conf import settings

from itambox.capabilities import (
    ALWAYS_ON,
    BETA,
    CONTRACT_VERSION,
    ENABLED,
    SOURCE_ALWAYS,
    SOURCE_CONFIGURED,
    STABLE,
    ActivationState,
    Capability,
    registry,
)


def _asset_request_procurement_probe():
    thresholds = getattr(settings, "ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS", None)
    if thresholds is None:
        thresholds = getattr(settings, "REQUISITION_AUTO_APPROVAL_THRESHOLDS", None)
    return ActivationState(active=bool(thresholds), value_present=thresholds is not None)


def _warn_legacy_auto_approval_setting():
    canonical = getattr(settings, "ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS", None)
    legacy = getattr(settings, "REQUISITION_AUTO_APPROVAL_THRESHOLDS", None)
    if canonical is None and legacy is not None:
        py_warnings.warn(
            "REQUISITION_AUTO_APPROVAL_THRESHOLDS is deprecated; configure "
            "ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS instead. "
            "The legacy fallback will be removed in ITAMbox 2.0.",
            UserWarning,
            stacklevel=2,
        )


class ProcurementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "procurement"

    def ready(self):
        _warn_legacy_auto_approval_setting()
        self._register_capabilities()

    def _register_capabilities(self):
        # ready() runs again when a test swaps INSTALLED_APPS, and a two-part
        # declaration must be finishable if it ever fails between the two.
        registry.register_all(self._capabilities())

    def _capabilities(self):
        return (
            Capability(
                key="procurement.core",
                title="Purchase Orders and Contracts",
                owning_area="area:procurement",
                maturity=STABLE,
                security_critical=False,
                activation=ALWAYS_ON,
                activation_probe=None,
                activation_source=SOURCE_ALWAYS,
                owns=(
                    "procurement.Contract",
                    "procurement.PurchaseOrder",
                    "procurement.PurchaseOrderLine",
                ),
                docs_url="development/capability-registry.md",
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="procurement.requisition_seam",
                title="Asset Request Procurement Seam",
                owning_area="area:procurement",
                maturity=BETA,
                security_critical=False,
                activation=ENABLED,
                activation_probe=_asset_request_procurement_probe,
                activation_source=SOURCE_CONFIGURED,
                owns=("procurement.FulfillmentLink",),
                docs_url="development/capability-registry.md",
                limitations=(
                    "The asset-request to purchase-order-line reservation flow is incomplete; "
                    "partial fulfilment may need manual reconciliation.",
                    "Auto-approval thresholds are process-wide, not per tenant.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
        )
