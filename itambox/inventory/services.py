from typing import Any, Optional, Tuple

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from core.context import get_current_user
from core.managers import get_current_tenant
from organization.access import authorize_tenant_operation, resolve_stock_access, resolved_shared_stock_ids
from organization.models import Location, Tenant, TenantResourceGrant

from .models import (
    AccessoryAssignment,
    AccessoryStock,
    ComponentAllocation,
    ComponentStock,
    ConsumableAssignment,
    ConsumableStock,
)
from .models_assignment_write import authorized_assignment_hard_purge, authorized_assignment_write

# Compatibility re-export: adjust_inventory_stock moved to the leaf module
# inventory.stock so inventory.models can call it without importing this
# module (issue #87, phase D). The published import path stays valid.
from .stock import adjust_inventory_stock  # noqa: F401  isort:skip

# Audit operation names. An actorless caller must hold a TaskContext system
# authorization issued for exactly one of these; the string is part of the
# capability, so callers name the constant instead of retyping the literal.
CHECKOUT_OPERATION = "inventory.checkout"
COMPONENT_ALLOCATION_OPERATION = "inventory.component_allocation"
SYSTEM_PROVENANCE_FIELDS = (
    "system_authorization_operation",
    "system_authorization_reason",
)
ASSIGNMENT_MODELS = (AccessoryAssignment, ComponentAllocation, ConsumableAssignment)


def purge_inventory_assignment(assignment):
    """Physically remove one soft-deleted assignment without restoring stock twice."""

    model = type(assignment)
    if model not in ASSIGNMENT_MODELS:
        raise ValidationError(_("Unsupported assignment purge model."))
    with transaction.atomic():
        assignment = model._base_manager.select_for_update().get(pk=assignment.pk)
        if assignment.deleted_at is None:
            raise ValidationError(_("Only a soft-deleted assignment can be permanently purged."))
        assignment_pk = assignment.pk
        with authorized_assignment_hard_purge(assignment):
            assignment.delete(force_hard_delete=True)
        return assignment_pk


def _system_authorization_provenance(actor, system_authorization, supplied_fields=()):
    if set(SYSTEM_PROVENANCE_FIELDS) & set(supplied_fields):
        raise ValidationError(_("System authorization provenance is derived by the checkout service."))
    if actor is not None:
        return dict.fromkeys(SYSTEM_PROVENANCE_FIELDS)
    return {
        "system_authorization_operation": system_authorization.operation,
        "system_authorization_reason": system_authorization.reason,
    }


def _optional_assigned_date(assigned_date):
    return {} if assigned_date is None else {"assigned_date": assigned_date}


def _reject_unsupported_assignment_fields(fields):
    if fields:
        raise ValidationError(_("Unsupported assignment fields: %(fields)s") % {"fields": ", ".join(sorted(fields))})


def checkout_inventory_item(
    item: Any,
    qty: int,
    holder: Optional[Any] = None,
    location: Optional[Any] = None,
    asset: Optional[Any] = None,
    user: Optional[Any] = None,
    notes: str = "",
    assigned_date: Optional[Any] = None,
    source_location: Optional[Any] = None,
    request: Optional[Any] = None,
    system_authorization: Optional[Any] = None,
    **kwargs: Any,
) -> Any:
    if not holder and not location and not asset:
        raise ValidationError(_("Either holder, location, or asset must be specified."))
    holder, location, asset = validate_checkout_targets(holder, location, asset)

    item_class_name = item.__class__.__name__
    if item_class_name == "Accessory":
        assignment_model = AccessoryAssignment
        stock_model = AccessoryStock
        item_field = "accessory"
    elif item_class_name == "Consumable":
        assignment_model = ConsumableAssignment
        stock_model = ConsumableStock
        item_field = "consumable"
    else:
        assignment_model = ComponentAllocation
        stock_model = ComponentStock
        item_field = "component"

    active_tenant = get_current_tenant()
    actor = user or get_current_user()
    perm = f"{assignment_model._meta.app_label}.add_{assignment_model._meta.model_name}"
    if not authorize_tenant_operation(
        actor,
        active_tenant,
        perm,
        system_authorization=system_authorization,
        system_operation=CHECKOUT_OPERATION,
    ):
        raise ValidationError(_("Checkout is not authorized in the active tenant."))
    provenance = _system_authorization_provenance(actor, system_authorization, kwargs)
    _reject_unsupported_assignment_fields(kwargs)

    with transaction.atomic():
        # Lock the row to prevent concurrent overallocation. _base_manager:
        # the item may live in another tenant than the active one (granted
        # cross-tenant checkout) — callers have already resolved the item
        # through an authorized surface.
        item = type(item)._base_manager.select_for_update().get(pk=item.pk)
        source_stock = None
        if source_location is not None:
            source_location = Location._base_manager.get(
                pk=source_location.pk,
                deleted_at__isnull=True,
                tenant__deleted_at__isnull=True,
            )
            source_stock = (
                stock_model._base_manager.select_for_update()
                .filter(**{item_field: item, "location": source_location})
                .select_related("location")
                .first()
            )

        # ADR-0001 phase 3/4: checking out from a pool owned by another
        # tenant requires a live TenantResourceGrant with 'use' — resolved
        # BEFORE any availability information is disclosed. The exact grant
        # used is recorded on the assignment (provenance).
        resource_grant = resolve_grant_for_checkout(
            item,
            item_field,
            stock_model,
            assignment_model,
            source_location,
            user=user,
            system_authorization=system_authorization,
        )

        # Item-level availability spans the ACTIVE tenant's pools (the related
        # managers are tenant-scoped); for an authorized cross-tenant checkout
        # the owner's pool is deliberately outside that view, so the pool-
        # specific check below is the meaningful gate instead.
        if resource_grant is None:
            if not item.allow_overallocate and item.available < qty:
                raise ValidationError(_("No stock available for checkout."))

        if source_location:
            # The concrete pool row is already locked above; this is the
            # authoritative availability value for the selected source.
            loc_stock = source_stock.qty if source_stock is not None else 0
            if not item.allow_overallocate and loc_stock < qty:
                raise ValidationError(
                    _("Insufficient stock at %(location)s. Available: %(available)s, Requested: %(requested)s")
                    % {"location": source_location, "available": loc_stock, "requested": qty}
                )

        assignment_values = {
            "assigned_holder": holder,
            "assigned_location": location,
            "assigned_asset": asset,
            "from_location": source_location,
            "qty": qty,
            "notes": notes,
            "resource_grant": resource_grant,
            **provenance,
            **_optional_assigned_date(assigned_date),
            item_field: item,
        }
        assignment = assignment_model(
            **assignment_values,
        )
        with authorized_assignment_write(assignment):
            assignment.save()
    return assignment


def validate_checkout_targets(holder, location, asset):
    active_tenant = get_current_tenant()
    active_tenant_id = getattr(active_tenant, "pk", None)
    if (
        active_tenant_id is None
        or not Tenant._base_manager.filter(
            pk=active_tenant_id,
            deleted_at__isnull=True,
        ).exists()
    ):
        raise ValidationError(_("Checkout requires a live active tenant."))
    persisted_targets = []
    for target in (holder, location, asset):
        if target is None:
            persisted_targets.append(None)
            continue
        target_id = getattr(target, "pk", None)
        filters = {"pk": target_id, "tenant_id": active_tenant_id}
        if any(field.name == "deleted_at" for field in target._meta.fields):
            filters["deleted_at__isnull"] = True
        persisted_target = type(target)._base_manager.filter(**filters).first()
        if target_id is None or persisted_target is None:
            raise ValidationError(_("Checkout targets must belong to the active tenant."))
        persisted_targets.append(persisted_target)
    return tuple(persisted_targets)


def create_component_allocation(
    component,
    qty,
    *,
    holder=None,
    location=None,
    asset=None,
    user=None,
    notes="",
    system_authorization=None,
    system_allow_overallocate=False,
):
    """Create a target-only component allocation through the authorized seam."""
    holder, location, asset = validate_checkout_targets(holder, location, asset)
    active_tenant = get_current_tenant()
    actor = user or get_current_user()
    if not authorize_tenant_operation(
        actor,
        active_tenant,
        "inventory.add_componentallocation",
        system_authorization=system_authorization,
        system_operation=COMPONENT_ALLOCATION_OPERATION,
    ):
        raise ValidationError(_("Component allocation is not authorized in the active tenant."))
    if system_allow_overallocate and actor is not None:
        raise ValidationError(_("Only trusted actorless system work may override component availability."))
    provenance = _system_authorization_provenance(actor, system_authorization)
    with transaction.atomic():
        component = type(component)._base_manager.select_for_update().get(pk=component.pk)
        if not component.allow_overallocate and not system_allow_overallocate and component.available < qty:
            raise ValidationError(_("No stock available for allocation."))
        assignment = ComponentAllocation(
            component=component,
            qty=qty,
            assigned_holder=holder,
            assigned_location=location,
            assigned_asset=asset,
            notes=notes,
            **provenance,
        )
        with authorized_assignment_write(assignment):
            assignment.save()
    return assignment


def update_component_allocation(
    assignment_pk,
    component,
    qty,
    *,
    holder=None,
    location=None,
    asset=None,
    source_location=None,
    user=None,
    notes="",
):
    """Update quantity/notes without rewriting assignment security provenance."""
    holder, location, asset = validate_checkout_targets(holder, location, asset)
    active_tenant = get_current_tenant()
    actor = user or get_current_user()
    if not authorize_tenant_operation(actor, active_tenant, "inventory.change_componentallocation"):
        raise ValidationError(_("Allocation update is not authorized in the active tenant."))

    with transaction.atomic():
        assignment = ComponentAllocation._base_manager.select_for_update().get(pk=assignment_pk)
        if assignment.deleted_at is not None or assignment.target_tenant_id != active_tenant.pk:
            raise ValidationError(_("Allocation update is outside the active tenant boundary."))

        requested_shape = (
            getattr(component, "pk", None),
            getattr(holder, "pk", None),
            getattr(location, "pk", None),
            getattr(asset, "pk", None),
            getattr(source_location, "pk", None),
        )
        persisted_shape = (
            assignment.component_id,
            assignment.assigned_holder_id,
            assignment.assigned_location_id,
            assignment.assigned_asset_id,
            assignment.from_location_id,
        )
        if requested_shape != persisted_shape:
            raise ValidationError(_("Component allocation item, source, and destination are immutable."))

        component = type(component)._base_manager.select_for_update().get(pk=assignment.component_id)
        extra_qty = qty - assignment.qty
        if extra_qty > 0 and not component.allow_overallocate and component.available < extra_qty:
            raise ValidationError(_("No stock available for allocation."))

        resolved_grant = resolve_grant_for_checkout(
            component,
            "component",
            ComponentStock,
            ComponentAllocation,
            source_location,
            user=actor,
        )
        if getattr(resolved_grant, "pk", None) != assignment.resource_grant_id:
            raise ValidationError(_("Allocation grant provenance cannot be rewritten."))

        provenance = (
            assignment.source_tenant_id,
            assignment.target_tenant_id,
            assignment.resource_grant_id,
            assignment.system_authorization_operation,
            assignment.system_authorization_reason,
        )
        assignment.qty = qty
        assignment.notes = notes
        with authorized_assignment_write(assignment):
            assignment.save()
        persisted_provenance = (
            assignment.source_tenant_id,
            assignment.target_tenant_id,
            assignment.resource_grant_id,
            assignment.system_authorization_operation,
            assignment.system_authorization_reason,
        )
        if persisted_provenance != provenance:
            raise ValidationError(_("Allocation security provenance changed during update."))
        return assignment


def shared_stock_union(queryset, stock_model):
    """Extend a tenant-scoped stock queryset with pools shared TO the active
    tenant via live TenantResourceGrants (ADR-0001 phase 4b: grantees may VIEW
    shared stock). Read surfaces only — mutation views keep pure scoping.
    No active tenant → unchanged queryset."""
    tenant = get_current_tenant()
    if tenant is None:
        return queryset
    user = get_current_user()
    perm = f"{stock_model._meta.app_label}.view_{stock_model._meta.model_name}"
    return queryset | stock_model._base_manager.filter(
        pk__in=resolved_shared_stock_ids(
            stock_model,
            tenant,
            user,
            TenantResourceGrant.ACCESS_VIEW,
            perm,
        ),
    )


def recipient_assignment_union(queryset, assignment_model):
    """Extend a tenant-scoped assignment queryset with live rows TARGETING the
    active tenant (ADR-0001 phase 4b: recipients may view inbound cross-tenant
    assignments and run the return workflow). No active tenant → unchanged."""
    # inline import: cycle: break an inventory <-> core import cycle at load
    from core.managers import get_current_tenant

    tenant = get_current_tenant()
    if tenant is None:
        return queryset
    return queryset | assignment_model._base_manager.filter(
        target_tenant=tenant,
        deleted_at__isnull=True,
    )


def resolve_grant_for_checkout(
    item,
    item_field,
    stock_model,
    assignment_model,
    source_location,
    user=None,
    system_authorization=None,
):
    """Authorize a checkout's source pool and return the covering grant.

    Same-tenant (or ownerless/global) sources return ``None`` — normal RBAC
    at the view layer is the gate there. A pool owned by another tenant than
    the active one is authorized through ``resolve_stock_access`` (grant +
    access level + the acting user's RBAC in the active tenant) and the
    exact grant row is returned for provenance. Shared by the item checkout
    flow and the kit checkout flow.
    """
    if source_location is None:
        return None
    stock_row = (
        stock_model._base_manager.filter(**{item_field: item, "location": source_location})
        .select_related("location")
        .first()
    )
    if stock_row is None:
        return None  # no concrete pool yet — nothing to authorize against
    active_tenant = get_current_tenant()
    owner_tenant_id = stock_row.location.tenant_id
    if active_tenant is None or owner_tenant_id is None or owner_tenant_id == active_tenant.pk:
        return None
    perm = f"{assignment_model._meta.app_label}.add_{assignment_model._meta.model_name}"
    decision = resolve_stock_access(
        user or get_current_user(),
        stock_row,
        TenantResourceGrant.ACCESS_USE,
        perm,
        active_tenant=active_tenant,
        system_authorization=system_authorization,
        system_operation=CHECKOUT_OPERATION,
        lock_grant=True,
    )
    if not decision.allowed:
        raise ValidationError(
            _(
                "Cross-tenant checkout denied (%(reason)s): the owning tenant "
                "must share this stock pool via a resource grant."
            )
            % {"reason": decision.reason}
        )
    return decision.grant


def checkin_accessory(assignment_pk: Any, user: Optional[Any] = None) -> Tuple[Any, int, Any]:
    return _checkin_assignment(
        AccessoryAssignment,
        AccessoryStock,
        "accessory",
        assignment_pk,
        user,
        "inventory.change_accessory",
    )


def checkin_component(assignment_pk: Any, user: Optional[Any] = None) -> Tuple[Any, int, Any]:
    return _checkin_assignment(
        ComponentAllocation,
        ComponentStock,
        "component",
        assignment_pk,
        user,
        "inventory.change_component",
    )


def _checkin_assignment(assignment_model, stock_model, item_field, assignment_pk, user, perm):
    with transaction.atomic():
        assignment = _authorized_return_assignment(
            assignment_model,
            assignment_pk,
            user,
            perm,
            lock=True,
        )
        item = getattr(assignment, item_field)
        qty = assignment.qty
        recipient = assignment.assigned_holder or assignment.assigned_location or assignment.assigned_asset
        if assignment.from_location_id:
            stock_model._base_manager.select_for_update().filter(
                **{
                    item_field: item,
                    "location_id": assignment.from_location_id,
                }
            ).first()
        with authorized_assignment_write(assignment):
            assignment.delete()
        return item, qty, recipient


def _authorized_return_assignment(assignment_model, assignment_pk, user, perm, *, lock=False):
    active_tenant = get_current_tenant()
    active_tenant_id = getattr(active_tenant, "pk", None)
    live_active = (
        active_tenant_id is not None
        and Tenant._base_manager.filter(
            pk=active_tenant_id,
            deleted_at__isnull=True,
        ).exists()
    )
    queryset = assignment_model._base_manager.filter(deleted_at__isnull=True)
    if lock:
        queryset = queryset.select_for_update()
    if live_active:
        queryset = queryset.filter(Q(source_tenant_id=active_tenant_id) | Q(target_tenant_id=active_tenant_id))
    else:
        queryset = queryset.none()
    assignment = get_object_or_404(queryset, pk=assignment_pk)
    if user is None or not user.has_perm(perm, obj=active_tenant):
        raise PermissionDenied
    return assignment
