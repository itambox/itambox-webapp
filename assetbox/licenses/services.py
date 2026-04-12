from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
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
        raise ValidationError("Either asset or assigned_holder must be specified.")
    if asset and assigned_holder:
        raise ValidationError("Cannot assign to both asset and assigned_holder.")

    with transaction.atomic():
        # Lock the license row to prevent TOCTOU race conditions under concurrent load
        lic = License.objects.select_for_update().get(pk=license_obj.pk)
        
        if lic.available_seats < 1:
            raise ValidationError("No available seats left for this software license.")
            
        assignment = LicenseSeatAssignment.objects.create(
            license=lic,
            asset=asset,
            assigned_holder=assigned_holder,
            notes=notes
        )
        
        # Symmetrically record transaction action message on changelog logger mixin
        lic._changelog_action = 'update'
        lic._changelog_message = f"Checked out seat to {asset or assigned_holder}."
        lic.save(update_fields=[]) # Trigger pre_save/post_save signals for change logging

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
        
        # Symmetrically record transaction action message on changelog logger mixin
        lic._changelog_action = 'update'
        lic._changelog_message = f"Checked in seat from {target}."
        lic.save(update_fields=[]) # Trigger pre_save/post_save signals for change logging

        return {'message': f"License seat for '{lic.name}' checked in."}
