from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from django.core.exceptions import FieldError, ValidationError
from django.utils.text import slugify

from core.tasks.context import TaskContext

_FIELD_FORMAT_MAP = {
    "TEXT": "text",
    "TEXTAREA": "text",
    "NUMERIC": "decimal",
    "DATE": "date",
    "BOOLEAN": "boolean",
    "CHECKBOX": "boolean",
    "LIST": "single-select",
    "LISTBOX": "single-select",
    "RADIO": "single-select",
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


def _canonical_snipeit_decimal(definition, value):
    scale = definition.decimal_scale
    if scale is None or not 0 <= scale <= 6:
        raise ValidationError("The decimal definition is invalid.", code="INVALID_RANGE")
    raw = str(value).strip()
    if "e" in raw.casefold() or raw.startswith("+"):
        raise ValidationError("Enter a base-10 decimal without an exponent.", code="INVALID_VALUE")
    try:
        decimal_value = Decimal(raw)
        quantized = decimal_value.quantize(Decimal(1).scaleb(-scale))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Enter a decimal value.", code="INVALID_VALUE") from exc
    if quantized != decimal_value:
        raise ValidationError("Enter a decimal value at the defined scale.", code="INVALID_VALUE")
    if definition.minimum_value is not None and quantized < definition.minimum_value:
        raise ValidationError("The value is below the allowed minimum.", code="INVALID_RANGE")
    if definition.maximum_value is not None and quantized > definition.maximum_value:
        raise ValidationError("The value is above the allowed maximum.", code="INVALID_RANGE")
    return format(quantized, f".{scale}f")


def _canonical_snipeit_integer(definition, value):
    if isinstance(value, bool):
        raise ValidationError("Enter an integer.", code="INVALID_TYPE")
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError as exc:
            raise ValidationError("Enter an integer.", code="INVALID_TYPE") from exc
    if not isinstance(value, int):
        raise ValidationError("Enter an integer.", code="INVALID_TYPE")
    if definition.minimum_value is not None and Decimal(value) < definition.minimum_value:
        raise ValidationError("The value is below the allowed minimum.", code="INVALID_RANGE")
    if definition.maximum_value is not None and Decimal(value) > definition.maximum_value:
        raise ValidationError("The value is above the allowed maximum.", code="INVALID_RANGE")
    return value


def _canonical_snipeit_boolean(value):
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, bool):
        return value
    raise ValidationError("Enter a boolean value.", code="INVALID_TYPE")


def _snipeit_choice_key(definition, value):
    if not isinstance(value, str) or definition.choice_set is None:
        raise ValidationError("Select a valid choice.", code="INVALID_CHOICE")
    choices = list(definition.choice_set.choices.filter(lifecycle="active"))
    key_matches = [choice.key for choice in choices if choice.key == value]
    if len(key_matches) == 1:
        return key_matches[0]
    label_matches = [choice.key for choice in choices if choice.label == value]
    if len(label_matches) == 1:
        return label_matches[0]
    raise ValidationError("Select a valid choice.", code="INVALID_CHOICE")


def _canonical_snipeit_boolean_field(definition, value):
    return _canonical_snipeit_boolean(value)


def _canonical_snipeit_date(definition, value):
    parsed = _parse_date(str(value).strip())
    if parsed is None:
        raise ValidationError("Enter a date.", code="INVALID_VALUE")
    return parsed.isoformat()


def _canonical_snipeit_single_select(definition, value):
    return _snipeit_choice_key(definition, value)


def _canonical_snipeit_multi_select(definition, value):
    values = [item.strip() for item in value.splitlines() if item.strip()] if isinstance(value, str) else value
    if not isinstance(values, list):
        raise ValidationError("Select unique valid choices.", code="INVALID_TYPE")
    return [_snipeit_choice_key(definition, item) for item in values]


def _canonical_snipeit_text(definition, value):
    if not isinstance(value, str):
        raise ValidationError("Enter text.", code="INVALID_TYPE")
    if definition.text_max_length is not None and len(value) > definition.text_max_length:
        raise ValidationError("The text is too long.", code="INVALID_RANGE")
    return value


def canonicalize_snipeit_custom_field_value(definition, value):
    handlers = {
        "integer": _canonical_snipeit_integer,
        "decimal": _canonical_snipeit_decimal,
        "boolean": _canonical_snipeit_boolean_field,
        "date": _canonical_snipeit_date,
        "single-select": _canonical_snipeit_single_select,
        "multi-select": _canonical_snipeit_multi_select,
        "text": _canonical_snipeit_text,
    }
    handler = handlers.get(definition.field_type)
    return handler(definition, value) if handler else value


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
