from django.apps import AppConfig

from core.features import settings_probe
from itambox.capabilities import (
    ALWAYS_ON,
    BETA,
    CONTRACT_VERSION,
    ENABLED,
    SOURCE_ALWAYS,
    SOURCE_CONFIGURED,
    STABLE,
    Capability,
    registry,
)


class ProcurementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "procurement"

    def ready(self):
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
                title="Requisition Fulfillment Seam",
                owning_area="area:procurement",
                maturity=BETA,
                security_critical=False,
                activation=ENABLED,
                # Observation only. Auto-approval already runs on built-in
                # thresholds when the setting is absent, so the probe reports
                # active-on-defaults and never becomes a new gate.
                activation_probe=settings_probe("REQUISITION_AUTO_APPROVAL_THRESHOLDS", default=True),
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
