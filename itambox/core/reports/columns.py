"""Common report column labels.

Labels are resolved at compilation time so a worker's active translation is
honoured; providers only own stable column keys and their defaults.
"""

from django.utils.translation import gettext_lazy as _

from core.report_keys import REPORT_COLUMN_KEYS

_COLUMN_LABELS = {
    "asset_tag": _("Asset Tag"),
    "name": _("Asset Name"),
    "manufacturer": _("Manufacturer"),
    "model": _("Model"),
    "serial_number": _("Serial Number"),
    "status": _("Status Label"),
    "location": _("Location"),
    "assigned_to": _("Asset Holder"),
    "purchase_cost": _("Purchase Cost"),
    "purchase_date": _("Purchase Date"),
    "warranty_months": _("Warranty (Months)"),
    "license_name": _("License Name"),
    "software": _("Software"),
    "seats": _("Total Seats"),
    "assigned_seats": _("Assigned Seats"),
    "available_seats": _("Available Seats"),
    "utilization_rate": _("Utilization Rate"),
    "subscription_name": _("Subscription Name"),
    "provider": _("Provider"),
    "billing_cycle": _("Billing Cycle"),
    "cost": _("Cost"),
    "end_date": _("End Date"),
    "maintenance_asset": _("Asset"),
    "maintenance_type": _("Type"),
    "maintenance_status": _("Status"),
    "maintenance_cost": _("Cost"),
    "maintenance_start_date": _("Start Date"),
    "maintenance_completion_date": _("Completion Date"),
    "maintenance_downtime": _("Downtime (Days)"),
    "salvage_value": _("Salvage Value"),
    "depreciation_months": _("Depreciation Lifespan (Months)"),
    "current_value": _("Depreciated Value"),
    "software_name": _("Software Product"),
    "version": _("Version"),
    "category": _("Category"),
    "license_type": _("License Type"),
    "installed_count": _("Installed Count"),
    "contract_number": _("Contract #"),
    "contract_name": _("Contract Name"),
    "contract_type": _("Contract Type"),
    "contract_status": _("Contract Status"),
    "contract_supplier": _("Supplier"),
    "contract_start_date": _("Start Date"),
    "contract_end_date": _("End Date"),
    "contract_renewal_date": _("Renewal Date"),
    "contract_days_until_expiry": _("Days Until Expiry"),
    "contract_cost": _("Contract Cost"),
    "contract_billing_cycle": _("Billing Cycle"),
    "contract_auto_renew": _("Auto-Renew"),
    "contract_covered_assets": _("Covered Assets"),
    "contract_sla_response_time": _("SLA Response Time"),
    "contract_sla_resolution_time": _("SLA Resolution Time"),
    "contract_coverage_hours": _("Coverage Hours"),
    "warranty_asset": _("Asset"),
    "warranty_type": _("Warranty Type"),
    "warranty_provider": _("Provider"),
    "warranty_start_date": _("Start Date"),
    "warranty_end_date": _("End Date"),
    "warranty_days_remaining": _("Days Remaining"),
    "warranty_status": _("Status"),
    "warranty_cost": _("Warranty Cost"),
    "warranty_reference": _("Reference"),
    "disposal_asset": _("Asset"),
    "disposal_date": _("Disposal Date"),
    "disposal_method": _("Disposal Method"),
    "disposal_sanitization_method": _("Data Sanitization Method"),
    "disposal_sanitization_certificate": _("Sanitization Certificate"),
    "disposal_sanitized_by": _("Sanitized By"),
    "disposal_recipient": _("Recipient"),
    "disposal_proceeds": _("Proceeds"),
    "disposal_weee_compliant": _("WEEE Compliant"),
    "disposal_notes": _("Notes"),
    "hw_item_type": _("Item Type"),
    "hw_name": _("Name"),
    "hw_manufacturer": _("Manufacturer"),
    "hw_category": _("Category"),
    "hw_part_number": _("Part Number"),
    "hw_total_stock": _("Total Stock"),
    "hw_available": _("Available"),
    "hw_min_qty": _("Safety Threshold"),
    "hw_status": _("Stock Status"),
    "custody_asset": _("Asset"),
    "custody_holder": _("Holder"),
    "custody_status": _("Acceptance Status"),
    "custody_accepted_date": _("Accepted Date"),
    "custody_eula_version": _("EULA Version"),
    "custody_signature_provider": _("Signature Provider"),
    "custody_qms_reference": _("QMS Reference"),
    "custody_ip_address": _("IP Address"),
    "custody_created_date": _("Created Date"),
    "license_count": _("License Count"),
}

CANONICAL_COLUMN_KEYS = REPORT_COLUMN_KEYS

if frozenset(_COLUMN_LABELS) != CANONICAL_COLUMN_KEYS:
    raise RuntimeError("Report column labels and canonical machine keys are out of sync")


def label_for(column):
    """Resolve one column key to its display label in the active language.

    A report row is keyed by the same label its header carries, so providers
    and the header list must never resolve a column independently.
    """
    return str(_COLUMN_LABELS[column])


def headers_for(columns):
    """Resolve column keys to display labels in the caller's active language.

    Labels are stored as lazy translations so the worker's active language is
    honoured; resolving here keeps every consumer (HTML, CSV, XLSX, PDF) on
    plain strings.
    """
    return [label_for(column) for column in columns if column in CANONICAL_COLUMN_KEYS]
