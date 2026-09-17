from __future__ import annotations

import datetime
import logging
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.http import HttpRequest

    from organization.models import AssetHolder, Location

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from compliance.models import CustodyReceipt
from core.choices import ObjectChangeActionChoices
from core.context import get_current_membership, get_current_tenant, override_current_tenant_scope
from inventory.services import checkout_inventory_item, validate_checkout_targets
from licenses.models import LicenseSeatAssignment

from ..choices import StatusTypeChoices
from ..depreciation import compute_book_value
from ..models import Asset, AssetAssignment, AssetDisposal, StatusLabel

logger = logging.getLogger(__name__)


def checkout_asset(
    asset: Asset,
    holder: AssetHolder | None = None,
    location: Location | None = None,
    asset_target: Asset | None = None,
    user: AbstractBaseUser | None = None,
    request: HttpRequest | None = None,
    expected_checkin: datetime.date | None = None,
    notes: str = "",
    checkout_date: datetime.datetime | None = None,
    status: StatusLabel | None = None,
    is_loan: bool = False,
    due_date: datetime.date | None = None,
) -> AssetHolder | Location | Asset:
    target = holder or location or asset_target
    if not target:
        raise ValidationError(_("Either holder, location, or asset must be specified."))

    with transaction.atomic():
        # Lock the asset row to prevent concurrent overallocation or state issues
        asset = Asset.objects.select_for_update().get(pk=asset.pk)

        # Lifecycle guard: assets on order, in repair, or archived are not
        # deployable. Archived assets only allow archived->pending, so a checkout
        # (which targets a deployed status) would raise an illegal-transition
        # error deeper in save(); reject it here with a clear message.
        if asset.status and asset.status.type in (
            StatusTypeChoices.IN_REPAIR,
            StatusTypeChoices.ON_ORDER,
            StatusTypeChoices.ARCHIVED,
        ):
            raise ValidationError(
                _("Cannot check out an asset that is %(status)s.") % {"status": asset.status.get_type_display()}
            )

        # Disposal guard (#496): the disposal RECORD owns the state, not the
        # status label. A record whose asset status drifted (or a soft-deleted
        # evidence row) must still make the asset unassignable.
        _assert_no_active_disposal(asset)

        # Reservation guard: if the asset is reserved for a *different* holder during
        # the checkout window, block the checkout to preserve the reservation.
        if holder:
            from assets.models import AssetReservation, ReservationStatusChoices

            today = datetime.date.today()
            blocking = (
                AssetReservation.all_objects.select_for_update()
                .filter(
                    asset=asset,
                    status__in=[
                        ReservationStatusChoices.ACTIVE,
                        ReservationStatusChoices.PENDING,
                    ],
                    start_date__lte=today,
                    end_date__gte=today,
                )
                .exclude(reserved_for=holder)
                .first()
            )
            if blocking:
                raise ValidationError(
                    _(
                        "Asset is reserved for %(holder)s until %(date)s and cannot be checked out to a different holder."
                    )
                    % {"holder": blocking.reserved_for, "date": blocking.end_date}
                )

        if asset.active_assignment:
            checkin_asset(asset, user=user, notes="Auto-checkin for reassignment")

        original_status = asset.status

        resolved_status = status
        if not resolved_status:
            resolved_status = StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYED).first()
        if resolved_status:
            asset.status = resolved_status

        update_fields = ["status", "location"]
        if holder:
            # A person assignment changes responsibility, not the base location.
            update_fields = ["status"]
        elif location:
            asset.location = location
        elif asset_target:
            asset.location = asset_target.location

        asset._changelog_action = "checkout"
        asset._changelog_message = f"Checked out to {target}"
        asset.save(update_fields=update_fields)

        assignment_kwargs = {
            "asset": asset,
            "checked_out_by": user,
            "expected_checkin_date": expected_checkin,
            "notes": notes,
            "pre_checkout_status": original_status,
            "is_loan": is_loan,
            "due_date": due_date,
        }
        if holder:
            assignment_kwargs["assigned_user"] = holder
        elif location:
            assignment_kwargs["assigned_location"] = location
        elif asset_target:
            assignment_kwargs["assigned_asset"] = asset_target

        if checkout_date:
            assignment_kwargs["checked_out_at"] = _normalized_checkout_datetime(checkout_date)

        AssetAssignment.objects.create(**assignment_kwargs)

        category = asset.asset_type.category if asset.asset_type else None
        if holder and category:
            from compliance.models import CustodyTemplate

            resolved_template = None

            # Priority 1: Tenant-specific override for the category
            if holder.tenant:
                resolved_template = CustodyTemplate.objects.filter(
                    tenant=holder.tenant, category=category, is_active=True
                ).first()

            # Priority 2: Tenant Group-specific override (if tenant belongs to a group)
            if not resolved_template and holder.tenant and holder.tenant.group:
                resolved_template = CustodyTemplate.objects.filter(
                    tenant_group=holder.tenant.group, category=category, is_active=True
                ).first()

            # Priority 3: Global category template (only if allowed by settings)
            if not resolved_template:
                from django.conf import settings

                if getattr(settings, "ALLOW_GLOBAL_CUSTODY_TEMPLATES", True):
                    resolved_template = CustodyTemplate.objects.filter(
                        tenant__isnull=True, tenant_group__isnull=True, category=category, is_active=True
                    ).first()

            if resolved_template and resolved_template.require_acceptance:
                receipt_kwargs = {
                    "asset": asset,
                    "holder": holder,
                    "custody_template": resolved_template,
                    "signature_provider": resolved_template.signature_provider,
                    "eula_text": resolved_template.eula_text,
                    "disclaimer": resolved_template.disclaimer,
                    "qms_reference": resolved_template.qms_reference,
                }
                receipt = CustodyReceipt.objects.create(**receipt_kwargs)

                # Send email signature request link if configured. The
                # provider hand-off and the outgoing mail are external side
                # effects: they are scheduled for the OUTERMOST commit, so a
                # checkout that later rolls back (any failing sibling in a kit)
                # never sends. A mandatory holder address still rejects the
                # checkout itself and stays inside the transaction.
                if resolved_template.email_signature_request and request:
                    try:
                        from core.models import EmailSettings

                        email_config = EmailSettings.load()
                        if email_config and email_config.enabled and email_config.from_address:
                            recipient = holder.email
                            if not recipient:
                                raise ValidationError(_("The holder has no e-mail address."))
                            from compliance.registry import signature_providers

                            provider = signature_providers.get(receipt.signature_provider or "local")
                            transaction.on_commit(
                                partial(
                                    _run_scoped_custody_notification,
                                    get_current_tenant(),
                                    get_current_membership(),
                                    provider,
                                    asset,
                                    receipt,
                                    recipient,
                                    email_config.from_address,
                                    request,
                                )
                            )
                    except ValidationError:
                        raise
                    # broad except: boundary-isolation: custody notification failure must not roll back assignment
                    except Exception as exc:
                        logger.error(
                            "Custody notification failed for asset_id=%s receipt_id=%s tenant_id=%s actor_id=%s "
                            "exception_type=%s",
                            asset.pk,
                            receipt.pk,
                            asset.tenant_id,
                            getattr(getattr(request, "user", None), "pk", None),
                            type(exc).__name__,
                        )

    return target


def _normalized_checkout_datetime(value):
    """Bind an operator-supplied date to an aware midnight (check-in convention).

    The UI posts ``<input type="date">`` values; ``checked_out_at`` is a
    ``DateTimeField``. Reuse the exact convention ``checkin_asset`` applies to
    its ``checkin_date`` so both directions persist the same instant shape.
    """
    if isinstance(value, datetime.datetime):
        return value
    return timezone.make_aware(datetime.datetime.combine(value, datetime.time.min))


def _run_scoped_custody_notification(tenant, membership, provider, asset, receipt, recipient, from_address, request):
    """Run a scheduled custody notification under the scope it was scheduled in.

    ``transaction.on_commit`` callbacks can run after the caller restored its
    request context (in tests, after the capturing context exits), so the tenant
    scope active when the checkout was registered is re-entered around the
    callback — the notification must never observe a different tenant (or none).
    """
    with override_current_tenant_scope(tenant, membership):
        _send_custody_signature_request(provider, asset, receipt, recipient, from_address, request)


def _send_custody_signature_request(provider, asset, receipt, recipient, from_address, request):
    """Send one custody signature request; runs only after the outermost commit.

    Kept as a module-level seam so single-asset and kit checkouts share the
    exact same notification path. The boundary is inform-and-log: once the
    assignment is durable, a notification failure must never surface as a
    checkout failure, no matter which caller committed it.
    """
    try:
        sign_url = provider.initiate_signature(receipt, request)
        send_mail(
            subject=_("Asset Acceptance Required: %(name)s (%(tag)s)")
            % {
                "name": asset.name,
                "tag": asset.asset_tag,
            },
            message=_(
                "Custody has been assigned to you:\n\n"
                "  Asset: %(name)s\n"
                "  Asset Tag: %(tag)s\n"
                "  Serial: %(serial)s\n\n"
                "Accept custody using this link:\n%(url)s\n\n"
                "This link expires in 7 days."
            )
            % {
                "name": asset.name,
                "tag": asset.asset_tag,
                "serial": asset.serial_number or "N/A",
                "url": sign_url,
            },
            from_email=from_address,
            recipient_list=[recipient],
            fail_silently=False,
        )
    # broad except: boundary-isolation: custody notification failure must not surface after commit
    except Exception as exc:
        logger.error(
            "Custody notification failed for asset_id=%s receipt_id=%s tenant_id=%s actor_id=%s exception_type=%s",
            asset.pk,
            receipt.pk,
            asset.tenant_id,
            getattr(getattr(request, "user", None), "pk", None),
            type(exc).__name__,
        )


def checkin_asset(
    asset: Asset,
    user: AbstractBaseUser | None = None,
    notes: str = "",
    status: StatusLabel | None = None,
    location: Location | None = None,
    checkin_date: datetime.date | None = None,
    request: HttpRequest | None = None,
) -> str | None:
    active = asset.active_assignment
    if active:
        target = active.assigned_target
        with transaction.atomic():
            active.is_active = False

            today = checkin_date or datetime.date.today()

            if checkin_date:
                dt = datetime.datetime.combine(checkin_date, datetime.time.min)
                active.checked_in_at = timezone.make_aware(dt)
            else:
                active.checked_in_at = timezone.now()

            # Stamp returned_at for loan assignments so is_overdue resolves correctly.
            if active.is_loan and active.returned_at is None:
                active.returned_at = today

            active.checked_in_by = user
            if notes:
                active.notes = (active.notes + "\n" + notes).strip()
            active.save()

            revert_status = status
            if not revert_status:
                revert_status = active.pre_checkout_status
            if not revert_status:
                revert_status = StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYABLE).first()

            if revert_status:
                asset.status = revert_status
            # Only overwrite location when a destination was provided; a blank
            # location preserves the recorded base/storage location rather than clearing it.
            if location is not None:
                asset.location = location
            asset._changelog_action = "checkin"
            asset._changelog_message = f"Checked in from {target}"
            asset.save(update_fields=["status", "location"])

            return f"Checked in from: {target}"
    elif asset.location:
        with transaction.atomic():
            checked_in_from = asset.location
            revert_status = status
            if not revert_status:
                revert_status = StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYABLE).first()
            if revert_status:
                asset.status = revert_status
            if location is not None:
                asset.location = location
            asset._changelog_action = "checkin"
            asset._changelog_message = f"Checked in from Location: {checked_in_from}"
            asset.save(update_fields=["status", "location"])
            return f"Checked in from Location: {checked_in_from}"
    else:
        return None


#: Editable metadata of a disposal record. ``asset`` is deliberately absent: a
#: record's asset identity is immutable (issue #496).
DISPOSAL_METADATA_FIELDS = (
    "disposal_method",
    "disposal_date",
    "data_sanitization_method",
    "sanitization_certificate",
    "sanitized_by",
    "recipient",
    "proceeds",
    "currency",
    "weee_compliant",
    "notes",
)


def disposal_service_payload(data: Mapping) -> dict:
    """Map submitted record values onto the disposal service's keyword arguments.

    One shared mapping keeps the dedicated action, the quick-add/edit form, the
    REST API and the admin from each inventing their own defaults (#496).
    """
    return {
        "disposal_method": data["disposal_method"],
        "disposal_date": data["disposal_date"],
        "data_sanitization_method": data.get("data_sanitization_method") or "none",
        "sanitization_certificate": data.get("sanitization_certificate") or "",
        "sanitized_by": data.get("sanitized_by") or "",
        "recipient": data.get("recipient") or "",
        "proceeds": data.get("proceeds"),
        "currency": data.get("currency") or "",
        "weee_compliant": data.get("weee_compliant") or False,
        "notes": data.get("notes") or "",
    }


def _active_disposal_error(asset: Asset) -> ValidationError | None:
    """The clean rejection for an asset that already owns an active disposal (#496)."""
    if not AssetDisposal.all_objects.filter(asset=asset, cancelled_at__isnull=True).exists():
        return None
    return ValidationError(
        _("Asset '%(asset)s' already has an active disposal record. Cancel that disposal before recording a new one.")
        % {"asset": asset}
    )


def _assert_no_active_disposal(asset: Asset) -> None:
    """Reject an asset whose disposal lifecycle is owned by a record (#496).

    Kept as a helper so ``checkout_asset`` does not grow another decision branch
    (it is at the Flake8 C901 ceiling).
    """
    if _active_disposal_error(asset) is not None:
        raise ValidationError(_("Cannot check out an asset that has an active disposal record."))


def _validate_disposal_proceeds(asset: Asset, proceeds, currency: str) -> None:
    """Proceeds must be non-negative and in the asset's own currency.

    There is no FX source, so a foreign-currency or negative proceeds would
    corrupt the frozen sign-off value / TCO it flows into.
    """
    if proceeds is not None and proceeds < 0:
        raise ValidationError(_("Disposal proceeds cannot be negative."))
    if proceeds is not None and currency and getattr(asset, "currency", None) and currency != asset.currency:
        raise ValidationError(
            _("Disposal proceeds currency must match the asset's currency (no conversion is applied).")
        )


def _disposal_snapshot_value(asset: Asset, disposal: "AssetDisposal"):
    """The asset-side sign-off value of an ACTIVE disposal record (#496).

    Existing semantics, deliberately unchanged (book-value computation stays out
    of scope for #496): recorded proceeds win, otherwise the depreciated residual
    at the disposal date is frozen. Callers must clear ``disposed_at`` /
    ``disposal_value`` first, because compute_book_value() short-circuits to an
    already-frozen value.
    """
    if disposal.proceeds is not None:
        return disposal.proceeds
    return compute_book_value(asset, on_date=disposal.disposal_date) or Decimal("0.00")


def update_asset_disposal(
    disposal: "AssetDisposal",
    user,
    data: Mapping,
    request: HttpRequest | None = None,
) -> "AssetDisposal":
    """Amend the metadata of a disposal record through the one controlled path (#496).

    The asset identity is immutable, cancellation state is never writable here
    (that is ``cancel_asset_disposal``), and the audit trail is retained: the
    record keeps its pk, its evidence and its history.

    While the record is ACTIVE the asset-side snapshot is re-applied with the same
    rule a fresh disposal uses, so the asset cannot silently drift away from its
    own evidence. A cancelled record is history and never touches the asset.
    """
    changes = {name: data[name] for name in DISPOSAL_METADATA_FIELDS if name in data}
    if not changes:
        raise ValidationError(_("No disposal fields were submitted."))

    with transaction.atomic():
        asset = Asset._base_manager.select_for_update().get(pk=disposal.asset_id)
        locked = AssetDisposal.all_objects.select_for_update().get(pk=disposal.pk)

        previous_stamp = asset.disposed_at
        locked.snapshot()
        for name, value in changes.items():
            setattr(locked, name, value)
        _validate_disposal_proceeds(asset, locked.proceeds, locked.currency)
        locked.full_clean()
        locked._changelog_action = ObjectChangeActionChoices.ACTION_UPDATE
        locked._changelog_message = "Disposal metadata amended"
        locked.save(update_fields=[*changes, "updated_at"])

        if locked.is_active:
            asset.disposed_at = None
            asset.disposal_value = None
            asset.disposal_value = _disposal_snapshot_value(asset, locked)
            asset.disposed_at = previous_stamp or timezone.now()
            asset._changelog_action = ObjectChangeActionChoices.ACTION_UPDATE
            asset._changelog_message = f"Disposal metadata amended (record {locked.pk})"
            asset.save(update_fields=["disposal_value", "disposed_at", "updated_at"])

    return locked


def dispose_asset(
    asset: Asset,
    disposal_method: str,
    disposal_date,
    data_sanitization_method: str = "none",
    sanitization_certificate: str = "",
    sanitized_by: str = "",
    recipient: str = "",
    proceeds=None,
    currency: str = "",
    weee_compliant: bool = False,
    notes: str = "",
    user=None,
) -> "AssetDisposal":
    """Record the end-of-life disposal of an asset.

    Creates the ``AssetDisposal`` record, stamps ``disposed_at`` and
    ``disposal_value`` on the ``Asset``, and transitions the asset to an
    *archived* ``StatusLabel``. The archived transition is required: when no
    archived label is configured the call raises a translated ``ValidationError``
    **before** any mutation (no record, no stamp, no auto check-in) - the status
    is never silently left unchanged and no label is created automatically.

    An asset that already owns an ACTIVE disposal record is rejected (#496): a
    record is evidence and is never replaced. Correcting a mistake is an
    explicit cancellation (``cancel_asset_disposal``).

    The whole operation is wrapped in a database transaction; either
    everything succeeds or nothing is written.
    """
    with transaction.atomic():
        # Lock the asset row to prevent concurrent mutations
        asset = Asset._base_manager.select_for_update().get(pk=asset.pk)

        # Reject a second disposal while an ACTIVE record exists (#496) BEFORE any
        # side effect: the predecessor implementation hard-deleted that record and
        # replaced it, which silently destroyed GDPR/WEEE/SOC2 evidence. Cancelled
        # records are history and do not block a new disposal.
        duplicate_error = _active_disposal_error(asset)
        if duplicate_error is not None:
            raise duplicate_error

        # The archived transition is REQUIRED, not optional (#496 language review): abort
        # atomically before the auto check-in, the record and the asset stamp when the
        # estate has no archived status. Status labels are never created automatically.
        archived_label = StatusLabel.objects.filter(type=StatusTypeChoices.ARCHIVED).first()
        if archived_label is None:
            raise ValidationError(_("No archived status is configured, so the disposal cannot be recorded."))

        # Auto-checkin any active assignment before disposal
        if asset.active_assignment:
            checkin_asset(asset, user=user, notes="Auto-checkin for disposal")
            asset.refresh_from_db()

        disposal = AssetDisposal(
            asset=asset,
            disposal_method=disposal_method,
            disposal_date=disposal_date,
            data_sanitization_method=data_sanitization_method,
            sanitization_certificate=sanitization_certificate,
            sanitized_by=sanitized_by,
            recipient=recipient,
            proceeds=proceeds,
            currency=currency,
            weee_compliant=weee_compliant,
            notes=notes,
        )
        disposal.full_clean()
        try:
            disposal.save()
        except IntegrityError as exc:
            # A concurrent disposal can win the race between the existence check
            # above and this insert. The conditional unique constraint is the
            # authority for "one active record per asset", so a lost race becomes
            # the same clean rejection — never a database error surfaced to the
            # operator and never a replaced record (#496).
            error = _active_disposal_error(asset)
            if error is None:
                # Some other integrity failure: keep it visible as such.
                raise
            raise error from exc

        # Clear any prior ARCHIVE freeze before recomputing: an asset may still
        # carry the freeze stamped by an earlier archive transition (or by a
        # cancelled disposal), and compute_book_value() short-circuits to that
        # stale frozen value while disposed_at is non-null. Reset both so the
        # residual is recomputed fresh for this disposal date.
        asset.disposed_at = None
        asset.disposal_value = None

        # Proceeds must be non-negative and in the asset's own currency: there is no FX
        _validate_disposal_proceeds(asset, proceeds, currency)

        # Update the asset: stamp disposal fields and transition status. The
        # snapshot rule lives in exactly one place (``_disposal_snapshot_value``)
        # so an amended record cannot drift away from a fresh disposal.
        # Ordering matters: the residual must be computed BEFORE disposed_at is
        # set, because compute_book_value() short-circuits once it is.
        asset.disposal_value = _disposal_snapshot_value(asset, disposal)
        asset.disposed_at = timezone.now()

        if archived_label:
            asset.status = archived_label

        asset._changelog_action = "dispose"
        asset._changelog_message = f"Disposed via {disposal.get_disposal_method_display()} on {disposal_date}"
        asset.save(update_fields=["disposed_at", "disposal_value", "status"])

    return disposal


def cancel_asset_disposal(
    disposal: "AssetDisposal",
    user,
    reason: str,
    request: HttpRequest | None = None,
) -> "AssetDisposal":
    """Cancel an erroneous disposal while preserving its evidence (#496).

    The record keeps its identity, its data-sanitization evidence and its place
    in the disposal history; it gains who cancelled it, when, and why. Nothing is
    hard- or soft-deleted, so the audit trail keeps both the original disposal and
    the correction.

    The asset returns to a *pending* status with the disposal/archival freeze
    cleared, so depreciation re-evaluates through the existing semantics. It is
    never auto-deployed, and no prior assignment or requestability is restored —
    that takes a deliberate follow-up workflow.

    Lock order is asset first, then the disposal row, matching ``dispose_asset``
    so a cancellation and a concurrent disposal serialize instead of racing.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(_("A cancellation reason is required."))
    if user is None:
        raise ValidationError(_("A cancellation must record who cancelled it."))

    with transaction.atomic():
        asset = Asset._base_manager.select_for_update().get(pk=disposal.asset_id)
        locked = AssetDisposal.all_objects.select_for_update().get(pk=disposal.pk)

        if locked.asset_id != asset.pk:
            # Never mutate a record through the wrong asset's lifecycle.
            raise ValidationError(_("The disposal record does not belong to this asset."))
        if locked.cancelled_at is not None:
            raise ValidationError(_("This disposal has already been cancelled."))

        # Leaving the archived state needs the pending label (#496 language review): abort
        # before the record, the asset and the audit trail are touched. Never create it.
        pending_label = StatusLabel.objects.filter(type=StatusTypeChoices.PENDING).first()
        if pending_label is None:
            raise ValidationError(_("No pending status is configured, so the disposal cannot be cancelled."))

        locked.snapshot()
        locked.cancelled_at = timezone.now()
        locked.cancelled_by = user
        locked.cancellation_reason = reason
        locked._changelog_action = ObjectChangeActionChoices.ACTION_UPDATE
        locked._changelog_message = f"Disposal cancelled: {reason}"
        locked.save(update_fields=["cancelled_at", "cancelled_by", "cancellation_reason", "updated_at"])

        # Order matters: the record is cancelled FIRST, because Asset.clean()
        # refuses to move an asset out of `archived` while an active record exists
        # (#496).
        asset.disposed_at = None
        asset.disposal_value = None
        update_fields = ["disposed_at", "disposal_value", "updated_at"]
        if pending_label is not None and asset.status_id != pending_label.pk:
            # Existing semantics: archiving is left through archived -> pending
            # only. Never jump straight back to a deployable status.
            asset.status = pending_label
            update_fields.append("status")
        asset._changelog_action = ObjectChangeActionChoices.ACTION_UPDATE
        asset._changelog_message = f"Disposal cancelled (record {locked.pk}): {reason}"
        asset.save(update_fields=update_fields)

    return locked


def _kit_hardware_selection(kit_items, selected_assets):
    """Validate the operator's explicit device selection per hardware row.

    ``selected_assets`` maps ``KitItem.pk`` to ``Asset.pk`` and must cover
    every hardware row of this kit exactly once; stock-only kits pass an empty
    or omitted mapping. Returns a normalized ``{item_pk: asset_pk}`` mapping.
    Malformed payloads (non-mapping, or keys/values that are not plain ints)
    are rejected.
    """
    if selected_assets is None:
        selected_assets = {}
    if not isinstance(selected_assets, Mapping):
        raise ValidationError(_("The kit hardware selection is invalid."))

    selections = {}
    for key, value in selected_assets.items():
        if isinstance(key, bool) or isinstance(value, bool):
            raise ValidationError(_("The kit hardware selection is invalid."))
        if not isinstance(key, int) or not isinstance(value, int):
            raise ValidationError(_("The kit hardware selection is invalid."))
        selections[key] = value

    hardware_pks = {item.pk for item in kit_items if item.asset_type}
    if set(selections) - hardware_pks:
        raise ValidationError(_("The kit hardware selection contains entries that are not hardware rows."))
    for item in kit_items:
        if item.asset_type and item.pk not in selections:
            raise ValidationError(_("Select an asset for hardware item '%(item)s'.") % {"item": item})
    if len(set(selections.values())) != len(selections):
        raise ValidationError(_("Each hardware item must use a different device."))
    return selections


def _check_kit_hardware_eligibility(asset, item, target_tenant_id):
    """Reject a selected device that no longer matches the operator's pick."""
    if asset.asset_type_id != item.asset_type_id:
        raise ValidationError(_("The selected asset does not match hardware item '%(item)s'.") % {"item": item})
    if target_tenant_id is not None and asset.tenant_id != target_tenant_id:
        raise ValidationError(_("The selected asset belongs to another tenant than the checkout target."))
    if asset.active_assignment:
        raise ValidationError(_("The selected asset is already assigned and cannot be selected."))
    if not asset.status or asset.status.type != StatusTypeChoices.DEPLOYABLE:
        raise ValidationError(_("The selected asset is not in a deployable state."))


def _lock_kit_hardware(kit_items, selections, target_tenant_id):
    """Lock every selected device and revalidate it under the row locks.

    Locks are taken in a deterministic pk order. A device that went away, was
    retargeted, reassigned or left the deployable state between form render and
    submit is rejected here — never silently substituted, and never silently
    auto-checked-in from its current assignment.
    """
    locked = {}
    for asset_pk in sorted(set(selections.values())):
        try:
            locked[asset_pk] = Asset.objects.select_for_update().get(pk=asset_pk)
        except Asset.DoesNotExist:
            raise ValidationError(_("The selected asset is no longer available.")) from None

    resolved = {}
    for item in kit_items:
        if item.asset_type:
            asset = locked[selections[item.pk]]
            _check_kit_hardware_eligibility(asset, item, target_tenant_id)
            resolved[item.pk] = asset
    return resolved


def _lock_kit_license_pools(kit_items):
    """Lock each kit license pool once and confirm a seat is still free.

    Repeated license rows of one kit resolve to the same seat target (the
    holder or the first selected device), and a target holds at most one seat
    per license (unique-target constraint) — so the kit's demand per pool is
    exactly one seat. Checking each row against the pool in isolation treated
    repeated rows as separate demand, which overallocated a single-seat pool
    and drove one duplicate seat insert per repeated row into the constraint.
    """
    seen = set()
    for item in kit_items:
        if item.license and item.license_id not in seen:
            seen.add(item.license_id)
            lic = item.license.__class__.objects.select_for_update().get(pk=item.license_id)
            if lic.available_seats < 1:
                raise ValidationError(_("No available seats for software license '%(lic)s'.") % {"lic": lic})


def _assign_kit_license_once(item, holder, first_asset, notes, assigned):
    """Assign one seat per license pool, however often the kit lists it."""
    if item.license_id in assigned:
        return
    assigned.add(item.license_id)
    _assign_kit_license_seat(item, holder, first_asset, notes)


def _assign_kit_license_seat(item, holder, first_asset, notes):
    """Assign one kit license seat to the holder, or to the first kit device."""
    if holder:
        LicenseSeatAssignment.objects.create(license=item.license, assigned_holder=holder, notes=notes)
    elif first_asset:
        LicenseSeatAssignment.objects.create(license=item.license, asset=first_asset, notes=notes)
    else:
        raise ValidationError(
            _("License seat for '%(name)s' must be assigned to either a Holder or an Asset.")
            % {"name": item.license.name}
        )


def checkout_kit(
    kit,
    holder=None,
    location=None,
    user=None,
    notes="",
    source_location=None,
    request=None,
    system_authorizations=None,
    selected_assets=None,
    expected_checkin=None,
    checkout_date=None,
    status=None,
    is_loan=False,
    due_date=None,
    **kwargs,
):
    """Check out a whole kit, composing the individual fulfilment operations.

    Hardware rows are fulfilled by ``checkout_asset`` for the explicitly
    selected device (``selected_assets`` maps ``KitItem.pk`` to ``Asset.pk``,
    one distinct device per hardware row); stock families keep using the
    inventory services. Everything runs in one transaction: any component
    failure rolls back assignments, stock, license seats, custody receipts and
    audit rows together, and the scheduled custody notifications are discarded.
    """
    if not holder and not location:
        raise ValidationError(_("Either holder or location must be specified."))
    holder, location, _asset_target = validate_checkout_targets(holder, location, None)
    system_authorizations = system_authorizations or {}

    if is_loan and not due_date:
        raise ValidationError(_("A loan assignment requires a due date."))
    if status is not None and status.type != StatusTypeChoices.DEPLOYED:
        raise ValidationError(_("Status '%(status)s' is not a deployed status label.") % {"status": status})

    in_use_status = status or StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYED).first()
    if not in_use_status:
        raise ValidationError(_("No 'Deployed' Status Label exists. Configure one first."))

    target_tenant_id = (holder or location).tenant_id

    with transaction.atomic():
        # Materialize the kit's items exactly once, under a row lock, and reuse that
        # snapshot for both selection validation and the allocation pass. Re-reading
        # kit.items for the allocation pass reopened the very TOCTOU window the
        # locking pass exists to close: a kit item added, retargeted or removed by a
        # concurrent transaction between the two SELECTs was allocated without ever
        # being planned (an unplanned asset_type item raised KeyError; an unplanned
        # license item consumed an unchecked seat). of=("self",) locks the kit item
        # rows only: every target FK is nullable, and PostgreSQL rejects FOR UPDATE
        # against the nullable side of an outer join.
        kit_items = list(
            kit.items.select_related(
                "asset_type",
                "accessory",
                "license",
                "consumable",
                "component",
            )
            .select_for_update(of=("self",))
            .order_by("pk")
        )

        # 1. Lock all resources first to prevent race conditions (TOCTOU): the
        # selected devices (revalidated under their row locks) and every license
        # pool the kit consumes.
        selections = _kit_hardware_selection(kit_items, selected_assets)
        selected_hardware = _lock_kit_hardware(kit_items, selections, target_tenant_id)
        _lock_kit_license_pools(kit_items)

        # 2. Perform allocations safely under active locks
        kit_notes = f"Checked out via Kit {kit.name!r}. {notes}"
        first_selected_asset = next(iter(selected_hardware.values()), None)
        license_seats_assigned = set()

        for item in kit_items:
            if item.asset_type:
                # The individual-asset operation owns custody, reservations,
                # lifecycle and loan semantics; the kit only decides which
                # device is allocated.
                checkout_asset(
                    selected_hardware[item.pk],
                    holder=holder,
                    location=location,
                    user=user,
                    request=request,
                    expected_checkin=expected_checkin,
                    notes=kit_notes,
                    checkout_date=checkout_date,
                    status=in_use_status,
                    is_loan=is_loan,
                    due_date=due_date,
                )

            elif item.accessory or item.consumable or item.component:
                stock_item = item.accessory or item.consumable or item.component
                permission = {
                    "Accessory": "inventory.add_accessoryassignment",
                    "Consumable": "inventory.add_consumableassignment",
                    "Component": "inventory.add_componentallocation",
                }[stock_item.__class__.__name__]
                checkout_inventory_item(
                    stock_item,
                    item.qty,
                    holder=holder,
                    location=location,
                    source_location=source_location,
                    user=user,
                    notes=kit_notes,
                    system_authorization=system_authorizations.get(permission),
                )
            elif item.license:
                _assign_kit_license_once(item, holder, first_selected_asset, kit_notes, license_seats_assigned)
