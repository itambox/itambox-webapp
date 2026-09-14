"""Shared request fulfilment evidence and presentation semantics."""

from __future__ import annotations

import contextvars
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from uuid import UUID, uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.choices import ObjectChangeActionChoices
from core.context import get_current_request_id
from core.models import ObjectChange, write_object_change
from inventory.models import AccessoryAssignment, ComponentAllocation, ConsumableAssignment

from ..choices import RequestStatusChoices
from ..models import AssetAssignment, AssetRequest

FULFILLMENT_EVIDENCE_KEY = "_request_fulfillment"
FULFILLMENT_EVIDENCE_VERSION = 1
FULFILLMENT_METHOD_MANUAL = "manual"
FULFILLMENT_METHOD_CHECKOUT = "checkout"
_VALID_METHODS = {FULFILLMENT_METHOD_MANUAL, FULFILLMENT_METHOD_CHECKOUT}
_ALLOWED_TRANSACTION_MODELS = {
    "assets.AssetAssignment": AssetAssignment,
    "inventory.AccessoryAssignment": AccessoryAssignment,
    "inventory.ConsumableAssignment": ConsumableAssignment,
    "inventory.ComponentAllocation": ComponentAllocation,
}
_CLAIM_TARGET_REQUEST_ID = contextvars.ContextVar("claim_target_request_id", default=None)
_MISSING_EVIDENCE = object()
_TERMINAL_STATUSES = {
    RequestStatusChoices.FULFILLED,
    RequestStatusChoices.DENIED,
    RequestStatusChoices.CANCELLED,
}


@contextmanager
def claim_fulfillment_scope(request_pk: int):
    token = _CLAIM_TARGET_REQUEST_ID.set(request_pk)
    try:
        yield
    finally:
        _CLAIM_TARGET_REQUEST_ID.reset(token)


def claim_target_request_id() -> int | None:
    return _CLAIM_TARGET_REQUEST_ID.get()


def checkout_transaction_reference(
    instance: object,
    quantity: int | None = None,
    *,
    expected_item: object | None = None,
) -> dict[str, object]:
    """Return a validated server-observed transaction identity for audit metadata."""
    model = getattr(instance, "_meta", None)
    pk = getattr(instance, "pk", None)
    model_label = getattr(model, "label", None)
    if model_label not in _ALLOWED_TRANSACTION_MODELS or type(pk) is not int or pk <= 0:
        raise ValidationError(_("Checkout did not return an allowed persisted transaction."))
    if expected_item is not None:
        item_attr = getattr(instance, "_item_attr", None)
        if not item_attr or getattr(instance, f"{item_attr}_id", None) != expected_item.pk:
            raise ValidationError(_("Checkout transaction does not match the requested item."))
    reference: dict[str, object] = {"model": model_label, "pk": pk}
    observed_quantity = getattr(instance, "qty", None)
    if quantity is not None:
        if type(quantity) is not int or quantity <= 0:
            raise ValidationError(_("Checkout transaction has an invalid quantity."))
        if observed_quantity is not None and (type(observed_quantity) is not int or observed_quantity != quantity):
            raise ValidationError(_("Checkout transaction quantity does not match the request."))
        observed_quantity = quantity
    if observed_quantity is not None:
        if type(observed_quantity) is not int or observed_quantity <= 0:
            raise ValidationError(_("Checkout transaction has an invalid quantity."))
        reference["quantity"] = observed_quantity
    return reference


def _request_audit_id(request_id: UUID | None = None) -> UUID:
    ambient_id = request_id or get_current_request_id()
    return ambient_id if ambient_id else uuid4()


def _payload_is_valid(payload: object, request_pk: int, tenant_id: int | None = None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if type(payload.get("version")) is not int or payload.get("version") != FULFILLMENT_EVIDENCE_VERSION:
        return False
    if type(payload.get("request_id")) is not int or payload.get("request_id") != request_pk:
        return False
    method = payload.get("method")
    if not isinstance(method, str) or method not in _VALID_METHODS:
        return False
    if method == FULFILLMENT_METHOD_MANUAL:
        reason = payload.get("reason")
        return isinstance(reason, str) and bool(reason.strip())
    return _references_are_valid(payload.get("transactions"))


def _references_are_valid(references):
    if not isinstance(references, list) or not references:
        return False
    for reference in references:
        if not isinstance(reference, Mapping):
            return False
        if not isinstance(reference.get("model"), str) or reference["model"] not in _ALLOWED_TRANSACTION_MODELS:
            return False
        pk = reference.get("pk")
        if type(pk) is not int or pk <= 0:
            return False
        if "quantity" in reference:
            quantity = reference["quantity"]
            if type(quantity) is not int or quantity <= 0:
                return False
    return True


def _evidence_candidates(requests: Iterable[AssetRequest]) -> dict[tuple[int | None, int], dict[str, object]]:
    request_list = list(requests)
    if not request_list:
        return {}
    content_type = ContentType.objects.get_for_model(AssetRequest)
    scope = Q(pk__in=[])
    for request in request_list:
        tenant_filter = (
            {"tenant_id": request.tenant_id} if request.tenant_id is not None else {"tenant_id__isnull": True}
        )
        scope |= Q(changed_object_id=request.pk, **tenant_filter)
    changes = ObjectChange._base_manager.filter(
        scope,
        changed_object_type=content_type,
        action=ObjectChangeActionChoices.ACTION_UPDATE,
        postchange_data__has_key=FULFILLMENT_EVIDENCE_KEY,
    ).order_by("-time", "-pk")
    requested = {(request.tenant_id, request.pk) for request in request_list}
    evidence: dict[tuple[int | None, int], dict[str, object]] = {}
    resolved: set[tuple[int | None, int]] = set()
    for change in changes:
        key = (change.tenant_id, change.changed_object_id)
        if key not in requested or key in resolved:
            continue
        resolved.add(key)
        postchange_data = change.postchange_data or {}
        payload = postchange_data.get(FULFILLMENT_EVIDENCE_KEY) if isinstance(postchange_data, Mapping) else None
        if _payload_is_valid(payload, change.changed_object_id, change.tenant_id):
            evidence[key] = dict(payload)
    return evidence


def get_request_fulfillment_evidence(request_instance: AssetRequest) -> dict[str, object] | None:
    """Read only valid, tenant-bound, server-generated completion metadata."""
    return _evidence_candidates([request_instance]).get((request_instance.tenant_id, request_instance.pk))


def write_fulfillment_evidence(
    *,
    request_instance: AssetRequest,
    method: str,
    actor: object | None,
    prechange_data: dict[str, object],
    postchange_data: dict[str, object],
    request_id: UUID | None = None,
    reason: str | None = None,
    transactions: Iterable[Mapping[str, object]] = (),
) -> ObjectChange:
    """Write one full-snapshot update row containing structured completion proof."""
    if not isinstance(method, str) or method not in _VALID_METHODS:
        raise ValidationError(_("Unknown request fulfilment method."))
    payload: dict[str, object] = {
        "version": FULFILLMENT_EVIDENCE_VERSION,
        "method": method,
        "request_id": request_instance.pk,
    }
    if method == FULFILLMENT_METHOD_MANUAL:
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError(_("A reason is required for manual completion."))
        payload["reason"] = reason.strip()
    else:
        payload["transactions"] = [dict(reference) for reference in transactions]
        if not _payload_is_valid(payload, request_instance.pk, request_instance.tenant_id):
            raise ValidationError(_("Checkout evidence is incomplete."))

    post_snapshot = dict(postchange_data)
    post_snapshot[FULFILLMENT_EVIDENCE_KEY] = payload
    return write_object_change(
        instance=request_instance,
        action=ObjectChangeActionChoices.ACTION_UPDATE,
        user=actor,
        request_id=_request_audit_id(request_id),
        change_tenant=request_instance.tenant,
        prechange_data=dict(prechange_data),
        postchange_data=post_snapshot,
    )


def _append_response_note(existing: str, note: str) -> str:
    existing = (existing or "").strip()
    return f"{existing}\n{note}".strip() if existing else note


def _lock_request(request_instance: AssetRequest) -> AssetRequest:
    """Lock the submitted row without allowing a cross-tenant pk lookup."""
    filters = {
        "pk": request_instance.pk,
        "tenant_id": request_instance.tenant_id,
        "deleted_at__isnull": True,
    }
    return AssetRequest.objects.select_for_update().get(**filters)


def _completion_conflicts(
    evidence: Mapping[str, object] | None,
    *,
    method: str,
    transactions: list[Mapping[str, object]] | None = None,
) -> bool:
    if not evidence:
        return False
    if evidence.get("method") != method:
        return True
    if method == FULFILLMENT_METHOD_CHECKOUT:
        return evidence.get("transactions") != transactions
    return False


def _mark_locked_request(
    locked: AssetRequest,
    *,
    actor: object,
    method: str,
    reason: str | None = None,
    transactions: Iterable[Mapping[str, object]] | None = None,
    asset: object | None = None,
) -> AssetRequest:
    prechange_data = locked._serialize_for_change(locked)
    transactions = list(transactions or ())
    if asset is not None:
        locked.asset = asset
    locked.status = RequestStatusChoices.FULFILLED
    locked.response_date = timezone.now()
    locked.responded_by = actor
    if method == FULFILLMENT_METHOD_MANUAL:
        locked.response_notes = _append_response_note(locked.response_notes, f"Manual completion reason: {reason}")
    else:
        locked.response_notes = _append_response_note(
            locked.response_notes,
            f"Automatically fulfilled via assignment checkout transaction ID: {transactions[0]['pk']}.",
        )
    locked.save()
    write_fulfillment_evidence(
        request_instance=locked,
        method=method,
        actor=actor,
        prechange_data=prechange_data,
        postchange_data=locked._serialize_for_change(locked),
        reason=reason,
        transactions=transactions,
    )
    return locked


@transaction.atomic
def manually_complete_request(
    request_instance: AssetRequest,
    *,
    actor: object | None = None,
    user: object | None = None,
    reason: str,
    confirmed_no_handover: bool,
    request: object | None = None,
) -> AssetRequest:
    """Complete approved request units without assignment or stock side effects."""
    del request
    actor = actor or user
    if actor is None:
        raise ValidationError(_("An actor is required for manual completion."))
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise ValidationError(_("A non-whitespace reason is required for manual completion."))
    if type(confirmed_no_handover) is not bool or not confirmed_no_handover:
        raise ValidationError(_("Confirm that no assignment or stock booking will be generated."))

    locked = _lock_request(request_instance)
    if locked.status != RequestStatusChoices.APPROVED:
        raise ValidationError(_("Only approved requests can be marked fulfilled."))
    if not locked.is_group:
        existing_evidence = get_request_fulfillment_evidence(locked)
        if _completion_conflicts(existing_evidence, method=FULFILLMENT_METHOD_MANUAL):
            raise ValidationError(_("This request already has completion evidence."))
        return _mark_locked_request(locked, actor=actor, method=FULFILLMENT_METHOD_MANUAL, reason=reason)

    return _manually_complete_group(locked, actor, reason)


def _manually_complete_group(locked, actor, reason):
    children = list(
        AssetRequest._base_manager.select_for_update()
        .filter(
            parent_id=locked.pk,
            tenant_id=locked.tenant_id,
            deleted_at__isnull=True,
        )
        .order_by("pk")
    )
    if not children:
        raise ValidationError(_("The request group has no request units and cannot be completed."))
    open_children = [child for child in children if child.status not in _TERMINAL_STATUSES]
    unsupported = [child for child in open_children if child.status != RequestStatusChoices.APPROVED]
    if unsupported:
        raise ValidationError(_("Every open request unit must be approved before completion."))
    eligible = [child for child in open_children if child.status == RequestStatusChoices.APPROVED]
    if not eligible:
        raise ValidationError(_("The request group has no approved request unit to complete."))
    for child in eligible:
        existing_evidence = get_request_fulfillment_evidence(child)
        if _completion_conflicts(existing_evidence, method=FULFILLMENT_METHOD_MANUAL):
            raise ValidationError(_("A request unit already has conflicting completion evidence."))
        _mark_locked_request(child, actor=actor, method=FULFILLMENT_METHOD_MANUAL, reason=reason)

    if all(child.status in _TERMINAL_STATUSES for child in children):
        locked.status = RequestStatusChoices.FULFILLED
        locked.response_date = timezone.now()
        locked.responded_by = actor
        locked.response_notes = _append_response_note(
            locked.response_notes,
            f"Group manually completed without handover. Reason: {reason}",
        )
        locked.save(update_fields=["status", "response_date", "responded_by", "response_notes"])
    return locked


def _transaction_target_filters(req, holder_field):
    if req.assigned_user_id:
        return {f"{holder_field}_id": req.assigned_user_id, f"{holder_field}__tenant_id": req.tenant_id}
    if req.assigned_location_id:
        return {"assigned_location_id": req.assigned_location_id, "assigned_location__tenant_id": req.tenant_id}
    if req.assigned_asset_id:
        return {"assigned_asset_id": req.assigned_asset_id, "assigned_asset__tenant_id": req.tenant_id}
    return {f"{holder_field}__user_id": req.requester_id, f"{holder_field}__tenant_id": req.tenant_id}


def _transaction_filters(req, model, reference, actor, asset):
    filters = {"pk": reference["pk"], "deleted_at__isnull": True}
    if model is AssetAssignment:
        filters.update(
            asset__tenant_id=req.tenant_id,
            is_active=True,
            checked_out_by_id=getattr(actor, "pk", None),
            checked_out_at__gte=req.request_date,
        )
        if asset is not None or req.asset_id:
            filters["asset_id"] = asset.pk if asset is not None else req.asset_id
        else:
            filters["asset__asset_type_id"] = req.asset_type_id
        filters.update(_transaction_target_filters(req, "assigned_user"))
    else:
        item_field = model._item_attr
        item_id = getattr(req, f"{item_field}_id", None)
        if item_id is None or reference.get("quantity") != req.qty:
            raise ValidationError(_("Checkout evidence does not match the requested inventory quantity."))
        filters.update(
            {
                f"{item_field}_id": item_id,
                "qty": req.qty,
                "target_tenant_id": req.tenant_id,
                "assigned_date__gte": req.request_date,
            }
        )
        filters.update(_transaction_target_filters(req, "assigned_holder"))
    return filters


def _validate_checkout_binding(req, references, actor, asset):
    if not _references_are_valid(references) or len(references) != 1:
        raise ValidationError(_("Exactly one recorded checkout transaction is required per request unit."))
    reference = references[0]
    model = _ALLOWED_TRANSACTION_MODELS[reference["model"]]
    filters = _transaction_filters(req, model, reference, actor, asset)
    if not model._base_manager.select_for_update().filter(**filters).exists():
        raise ValidationError(_("Checkout evidence does not match a live handover for this request."))
    identity = {"model": reference["model"], "pk": reference["pk"]}
    content_type = ContentType.objects.get_for_model(AssetRequest)
    if (
        ObjectChange._base_manager.filter(
            changed_object_type=content_type,
            postchange_data___request_fulfillment__method=FULFILLMENT_METHOD_CHECKOUT,
            postchange_data___request_fulfillment__transactions__contains=[identity],
        )
        .exclude(changed_object_id=req.pk)
        .exists()
    ):
        raise ValidationError(_("This checkout already completed another request unit."))


@transaction.atomic
def complete_request_from_checkout(
    request_instance: AssetRequest,
    *,
    actor: object | None,
    transactions: Iterable[Mapping[str, object]],
    request: object | None = None,
    asset: object | None = None,
) -> AssetRequest:
    """Record one observed checkout as fulfilment, preserving existing proof."""
    del request
    locked = _lock_request(request_instance)
    if locked.status == RequestStatusChoices.PROCUREMENT:
        raise ValidationError(_("Receive procurement stock before recording handover."))
    transaction_list = [dict(reference) for reference in transactions]
    existing_evidence = get_request_fulfillment_evidence(locked)
    if existing_evidence:
        if _completion_conflicts(
            existing_evidence,
            method=FULFILLMENT_METHOD_CHECKOUT,
            transactions=transaction_list,
        ):
            raise ValidationError(_("Checkout evidence does not match the existing completion proof."))
        return locked
    if locked.status in _TERMINAL_STATUSES and locked.status != RequestStatusChoices.FULFILLED:
        raise ValidationError(_("A denied or cancelled request cannot be fulfilled."))
    if locked.status == RequestStatusChoices.FULFILLED:
        raise ValidationError(_("Fulfilled request is missing valid checkout evidence."))
    _validate_checkout_binding(locked, transaction_list, actor, asset)
    return _mark_locked_request(
        locked,
        actor=actor,
        method=FULFILLMENT_METHOD_CHECKOUT,
        transactions=transaction_list,
        asset=asset,
    )


def request_fulfillment_label(
    request_instance: AssetRequest,
    evidence: Mapping[str, object] | object | None = _MISSING_EVIDENCE,
    child_evidence: Mapping[int, Mapping[str, object]] | None = None,
    child_requests: Iterable[AssetRequest] | None = None,
) -> str:
    """Return the honest operational label shown by lists and detail pages."""
    if request_instance.is_group:
        if request_instance.status == RequestStatusChoices.APPROVED:
            return _("Approved: request units awaiting handover")
        if request_instance.status != RequestStatusChoices.FULFILLED:
            return str(request_instance.get_status_display())
        return _group_fulfillment_label(request_instance, child_evidence, child_requests)

    if request_instance.status == RequestStatusChoices.APPROVED and request_instance.asset_id:
        return _("Approved: allocated, awaiting handover")
    if request_instance.status == RequestStatusChoices.APPROVED:
        return _("Approved: awaiting handover")
    if request_instance.status != RequestStatusChoices.FULFILLED:
        return str(request_instance.get_status_display())
    if evidence is _MISSING_EVIDENCE:
        evidence = get_request_fulfillment_evidence(request_instance)
    if not evidence:
        return _("Fulfilled: Handover evidence not verified")
    if evidence.get("method") == FULFILLMENT_METHOD_MANUAL:
        return _("Fulfilled: Manually completed; no handover booked")
    return _("Fulfilled: handover recorded")


def _group_fulfillment_label(request_instance, child_evidence, child_requests):
    children = list(child_requests) if child_requests is not None else list(request_instance.sub_requests.all())
    if not children:
        return _("Fulfilled: Handover evidence not verified")
    open_children = [child for child in children if child.status not in _TERMINAL_STATUSES]
    fulfilled_children = [child for child in children if child.status == RequestStatusChoices.FULFILLED]
    if open_children:
        return _("Partial: request units still open")
    if not fulfilled_children:
        return _("Terminal request units: no handover booked")
    evidence_by_pk = child_evidence or {}
    methods = {(evidence_by_pk.get(child.pk) or {}).get("method") for child in fulfilled_children}
    if any(not evidence_by_pk.get(child.pk) for child in fulfilled_children):
        return _("Fulfilled: Handover evidence not verified")
    if methods == {FULFILLMENT_METHOD_CHECKOUT}:
        label = _("Fulfilled: all handovers recorded")
    elif methods == {FULFILLMENT_METHOD_MANUAL}:
        label = _("Fulfilled: Manually completed; no handover booked")
    else:
        label = _("Fulfilled: mixed manual and verified handover")
    if any(child.status in {RequestStatusChoices.DENIED, RequestStatusChoices.CANCELLED} for child in children):
        return _("Partial: terminal units remain; ") + label
    return label


def request_fulfillment_labels(requests: Iterable[AssetRequest]) -> dict[int, str]:
    """Build request labels with one child query and one batched audit query."""
    request_list = list(requests)
    if not request_list:
        return {}
    parent_ids = [request.pk for request in request_list if request.is_group]
    tenant_ids = {request.tenant_id for request in request_list}
    children = list(
        AssetRequest._base_manager.filter(
            parent_id__in=parent_ids,
            tenant_id__in=tenant_ids,
            deleted_at__isnull=True,
        ).order_by("pk")
    )
    all_requests = request_list + children
    evidence = _evidence_candidates(all_requests)
    children_by_parent: dict[int, list[AssetRequest]] = {}
    for child in children:
        children_by_parent.setdefault(child.parent_id, []).append(child)
    labels = {}
    for request in request_list:
        request_evidence = evidence.get((request.tenant_id, request.pk), {})
        child_rows = children_by_parent.get(request.pk, [])
        child_evidence = {
            child.pk: evidence[(child.tenant_id, child.pk)]
            for child in child_rows
            if (child.tenant_id, child.pk) in evidence
        }
        labels[request.pk] = request_fulfillment_label(
            request,
            evidence=request_evidence,
            child_evidence=child_evidence,
            child_requests=child_rows,
        )
    return labels


__all__ = [
    "FULFILLMENT_EVIDENCE_KEY",
    "FULFILLMENT_METHOD_CHECKOUT",
    "FULFILLMENT_METHOD_MANUAL",
    "checkout_transaction_reference",
    "claim_fulfillment_scope",
    "claim_target_request_id",
    "complete_request_from_checkout",
    "get_request_fulfillment_evidence",
    "manually_complete_request",
    "request_fulfillment_label",
    "request_fulfillment_labels",
    "write_fulfillment_evidence",
]
