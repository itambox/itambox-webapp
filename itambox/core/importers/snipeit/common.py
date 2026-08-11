from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from django.core.exceptions import FieldError
from django.utils.text import slugify

from core.tasks.context import TaskContext

_FIELD_FORMAT_MAP = {
    "TEXT": "text",
    "TEXTAREA": "text",
    "NUMERIC": "number",
    "DATE": "date",
    "BOOLEAN": "boolean",
    "CHECKBOX": "boolean",
    "LIST": "select",
    "LISTBOX": "select",
    "RADIO": "select",
}

_MAINTENANCE_TYPE_MAP = {
    "maintenance": "repair",
    "repair": "repair",
    "upgrade": "upgrade",
    "hardware support": "hardware_support",
    "software support": "software_support",
    "pat test": "calibration",
    "asset review": "calibration",
    "firmware update": "upgrade",
    "other": "repair",
}

_MAINTENANCE_STATUS_MAP = {
    "pending": "scheduled",
    "complete": "completed",
    "in progress": "in_progress",
}

_STATUS_TYPE_MAP = {
    "deployable": "deployable",
    "pending": "pending",
    "undeployable": "undeployable",
    "archived": "archived",
    "out of deployable": "deployed",
}

_CATEGORY_APPLIES_MAP = {
    "asset": {"asset": True},
    "accessory": {"accessory": True},
    "consumable": {"consumable": True},
    "component": {"component": True},
    "license": {"asset": True},
}

IMPORT_NOTE = "Imported from Snipe-IT"


def _parse_date(val: str | None) -> datetime.date | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(val, fmt).date()
        # broad except: boundary-isolation: malformed remote date values degrade to None
        except (ValueError, TypeError):
            pass
    return None


def _parse_decimal(val) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def _clean_field_name(db_column: str) -> str:
    """Strip _snipeit_ prefix and trailing _<id> from a Snipe-IT db_column_name."""
    name = db_column
    name = re.sub(r"^_snipeit_", "", name)
    name = re.sub(r"_\d+$", "", name)
    return name[:100]


def _unique_slug(model_class, name: str, extra: str = "") -> str:
    """Generate a unique slug for model_class by slugifying name, appending counter on collision."""
    base = (slugify(f"{name} {extra}") if extra else slugify(name)) or "imported"
    base = base[:90]
    slug = base
    counter = 1
    manager = model_class._base_manager
    while True:
        try:
            exists = manager.filter(slug=slug, deleted_at__isnull=True).exists()
        except FieldError:
            exists = manager.filter(slug=slug).exists()
        if not exists:
            break
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _nested_id(obj) -> int | None:
    if isinstance(obj, dict):
        return obj.get("id")
    return None


def _nested_str(obj, key="name") -> str:
    if isinstance(obj, dict):
        return obj.get(key) or ""
    return ""


def tenant_for(row, *, default_tenant, map_companies, tenants):
    if map_companies:
        cid = _nested_id(row.get("company"))
        if cid and cid in tenants:
            return tenants[cid]
    return default_tenant


class InventoryAssignmentGateway:
    def __init__(self, checkout_inventory_item: Callable, create_component_allocation: Callable, user) -> None:
        self._checkout_inventory_item = checkout_inventory_item
        self._create_component_allocation = create_component_allocation
        self.user = user

    def assign(self, item, qty, *, holder=None, asset=None) -> None:
        """Create one imported assignment through the sanctioned inventory service."""
        target = holder if holder is not None else asset
        with TaskContext(tenant_id=target.tenant_id, user_id=getattr(self.user, "pk", None)):
            if asset is not None:
                self._create_component_allocation(item, qty, asset=asset, user=self.user, notes=IMPORT_NOTE)
            else:
                self._checkout_inventory_item(item, qty, holder=holder, user=self.user, notes=IMPORT_NOTE)


class HardwareCheckoutGateway:
    def __init__(self, checkout_asset: Callable, user) -> None:
        self._checkout_asset = checkout_asset
        self.user = user

    def checkout(
        self,
        *,
        asset,
        holder=None,
        location=None,
        asset_target=None,
        status,
        tenant_id,
    ) -> None:
        with TaskContext(tenant_id=tenant_id, user_id=getattr(self.user, "pk", None)):
            if holder is not None:
                self._checkout_asset(
                    asset=asset,
                    holder=holder,
                    user=self.user,
                    status=status,
                    notes=IMPORT_NOTE,
                )
            elif location is not None:
                self._checkout_asset(
                    asset=asset,
                    location=location,
                    user=self.user,
                    status=status,
                    notes=IMPORT_NOTE,
                )
            elif asset_target is not None:
                self._checkout_asset(
                    asset=asset,
                    asset_target=asset_target,
                    user=self.user,
                    status=status,
                    notes=IMPORT_NOTE,
                )
