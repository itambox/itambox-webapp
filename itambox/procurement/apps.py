import warnings as py_warnings

from django.apps import AppConfig
from django.conf import settings

from itambox.capabilities import (
    ALWAYS_ON,
    CAPABILITY_REGISTRY_DOC_URL,
    CONTRACT_VERSION,
    SOURCE_ALWAYS,
    STABLE,
    Capability,
    registry,
)


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
        self._register_archive_handlers()

    def _register_archive_handlers(self):
        # inline imports: app-registry: the archive registry is populated once models are loaded.
        from core.archive_handlers import ArchiveBehaviour, ArchiveRelation, register_archive_handler
        from procurement.archive_services import archive_purchase_order, restore_purchase_order
        from procurement.models import PurchaseOrder

        register_archive_handler(
            PurchaseOrder._meta.label_lower,
            archive=archive_purchase_order,
            restore=restore_purchase_order,
            relations=(
                ArchiveRelation(
                    "procurement.purchaseorderline.purchase_order",
                    ArchiveBehaviour.ARCHIVE,
                    "archived with the order through their own save(); refused while a line has a live "
                    "fulfillment link",
                ),
                ArchiveRelation(
                    "procurement.contract.purchase_order",
                    ArchiveBehaviour.DETACH,
                    "contracts are unlinked from the order with their own audited update",
                ),
            ),
        )

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
                docs_url=CAPABILITY_REGISTRY_DOC_URL,
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="procurement.requisition_seam",
                title="Asset Request Procurement Seam",
                owning_area="area:procurement",
                maturity=STABLE,
                security_critical=False,
                activation=ALWAYS_ON,
                activation_probe=None,
                activation_source=SOURCE_ALWAYS,
                owns=("procurement.FulfillmentLink",),
                docs_url=CAPABILITY_REGISTRY_DOC_URL,
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
        )
