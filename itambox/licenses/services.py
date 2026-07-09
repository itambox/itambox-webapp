from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import License, LicenseSeatAssignment
from assets.models import Asset
from organization.models import AssetHolder

def checkout_license(
    license_obj: License,
    asset: Asset | None = None,
    assigned_holder: AssetHolder | None = None,
    notes: str = '',
    user = None,
    request = None,
    **kwargs
) -> LicenseSeatAssignment:
    if not asset and not assigned_holder:
        raise ValidationError(_("Either asset or assigned_holder must be specified."))
    if asset and assigned_holder:
        raise ValidationError(_("Cannot assign to both asset and assigned_holder."))

    with transaction.atomic():
        # Lock the license row to prevent TOCTOU race conditions under concurrent load
        lic = License.objects.select_for_update().get(pk=license_obj.pk)
        
        if lic.available_seats < 1:
            raise ValidationError(_("No available seats left for this software license."))

        # Reject a second active seat for the same target. The DB enforces this with
        # a partial UniqueConstraint, but checking here turns a duplicate request into
        # a clean ValidationError (HTTP 400) instead of an IntegrityError (HTTP 500).
        duplicate = LicenseSeatAssignment.objects.filter(license=lic)
        duplicate = (
            duplicate.filter(asset=asset) if asset is not None
            else duplicate.filter(assigned_holder=assigned_holder)
        )
        if duplicate.exists():
            raise ValidationError(
                _("%(target)s already holds a seat on this license.") % {
                    'target': asset or assigned_holder,
                }
            )

        assignment = LicenseSeatAssignment.objects.create(
            license=lic,
            asset=asset,
            assigned_holder=assigned_holder,
            notes=notes
        )

        # Record a changelog entry for the seat checkout. Nothing on the license row
        # itself changed, so a no-op save() would be short-circuited by
        # ChangeLoggingMixin (prechange == postchange) and the entry silently dropped.
        # Emit it directly via _log_change(), which is not subject to that equality
        # short-circuit, so the audit trail always captures the checkout.
        lic._log_change(
            action='update',
            message=f"Checked out seat to {asset or assigned_holder}.",
        )

        return assignment


def transfer_license_seat(
    assignment: LicenseSeatAssignment,
    new_license: License,
    user = None,
    request = None,
    **kwargs
) -> LicenseSeatAssignment:
    """Move an existing seat assignment onto a different License.

    D5-1: checkout_license()'s lock-then-check guard only ever ran when a seat was
    first created — a PATCH that repointed ``license_id`` at a different License went
    through the default ModelSerializer.update() with no capacity check at all,
    letting a target with fewer seats than active assignments silently over-fill.
    Mirrors checkout_license(): lock the destination License row, re-check
    available_seats and the no-duplicate-target invariant under that lock.
    """
    with transaction.atomic():
        # Lock the destination license row to prevent TOCTOU race conditions under
        # concurrent load, same as checkout_license().
        lic = License.objects.select_for_update().get(pk=new_license.pk)

        if lic.available_seats < 1:
            raise ValidationError(_("No available seats left for this software license."))

        target = assignment.asset or assignment.assigned_holder
        duplicate = LicenseSeatAssignment.objects.filter(license=lic).exclude(pk=assignment.pk)
        duplicate = (
            duplicate.filter(asset=assignment.asset) if assignment.asset_id is not None
            else duplicate.filter(assigned_holder=assignment.assigned_holder)
        )
        if duplicate.exists():
            raise ValidationError(
                _("%(target)s already holds a seat on this license.") % {'target': target}
            )

        old_license = assignment.license
        assignment.license = lic
        assignment.save(update_fields=['license'])

        # Record on both sides of the transfer. Nothing about either License row
        # itself changed (only the assignment's FK), so a no-op save() would be
        # short-circuited by ChangeLoggingMixin (see checkout_license() above) —
        # emit directly via _log_change() so both audit trails capture the move.
        lic._log_change(
            action='update',
            message=f"Received transferred seat for {target} from {old_license}.",
        )
        old_license._log_change(
            action='update',
            message=f"Seat for {target} transferred to {lic}.",
        )

        return assignment


def checkin_license_seat(
    assignment: LicenseSeatAssignment,
    user = None,
    request = None,
    **kwargs
) -> dict:
    with transaction.atomic():
        # Lock the assignment row to prevent concurrent checkin race conditions
        asgn = LicenseSeatAssignment.objects.select_for_update().get(pk=assignment.pk)
        lic = asgn.license
        
        target = asgn.asset or asgn.assigned_holder
        asgn.delete()

        # The license row is unchanged by a check-in, so rely on _log_change() rather
        # than a no-op save() that ChangeLoggingMixin would short-circuit away (see
        # checkout_license above). This guarantees the audit trail records the check-in.
        lic._log_change(
            action='update',
            message=f"Checked in seat from {target}.",
        )

        return {'message': _("License seat for '%(name)s' checked in.") % {"name": lic.name}}
