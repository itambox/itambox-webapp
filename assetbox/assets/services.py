from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import Asset, StatusLabel, ActivityLog, AssetAssignment
from compliance.models import CustodyReceipt
from inventory.models import AccessoryAssignment, ConsumableAssignment
from organization.models import AssetHolderAssignment
from licenses.models import LicenseSeatAssignment


def checkout_asset(asset, holder=None, location=None, asset_target=None, user=None, request=None, expected_checkin=None, notes=''):
    target = holder or location or asset_target
    if not target:
        raise ValidationError("Either holder, location, or asset must be specified.")

    with transaction.atomic():
        if asset.active_assignment:
            checkin_asset(asset, user=user, notes='Auto-checkin for reassignment')

        in_use_status = StatusLabel.objects.filter(type='deployed').first()
        if in_use_status:
            asset.status = in_use_status

        if holder:
            asset.location = None
        elif location:
            asset.location = location

        asset._changelog_action = 'checkout'
        asset._changelog_message = f"Checked out to {target}"
        asset.save(update_fields=['status', 'location'])

        AssetHolderAssignment.objects.filter(
            content_type=ContentType.objects.get_for_model(Asset),
            object_id=asset.pk
        ).delete()

        assignment = AssetAssignment.objects.create(
            asset=asset,
            assigned_to=target,
            checked_out_by=user,
            expected_checkin_date=expected_checkin,
            notes=notes,
        )

        ActivityLog.objects.create(
            asset=asset,
            action='checked_out',
            user=user,
            notes=f"Checked out to {target}. {notes}".strip()
        )

        category = asset.asset_type.category if asset.asset_type else None
        if holder and category and category.require_acceptance:
            receipt = CustodyReceipt.objects.create(
                asset=asset,
                holder=holder,
            )
            if category.email_eula and request:
                try:
                    from core.models import EmailSettings
                    email_config = EmailSettings.load()
                    if email_config and email_config.enabled and email_config.from_address:
                        recipient = holder.email
                        if not recipient:
                            recipient = email_config.test_recipient or email_config.from_address
                        if recipient:
                            sign_url = request.build_absolute_uri(
                                reverse('assets:custody_eula_sign', kwargs={'token': receipt.token})
                            )
                            send_mail(
                                subject=f'Asset Acceptance Required: {asset.name} ({asset.asset_tag})',
                                message=(
                                    f'You have been assigned custody of:\n\n'
                                    f'  Asset: {asset.name}\n'
                                    f'  Asset Tag: {asset.asset_tag}\n'
                                    f'  Serial: {asset.serial_number or "N/A"}\n\n'
                                    f'Please accept custody at the following link:\n{sign_url}\n\n'
                                    f'This link expires in 7 days.'
                                ),
                                from_email=email_config.from_address,
                                recipient_list=[recipient],
                                fail_silently=True,
                            )
                except Exception:
                    pass

    return target


def checkin_asset(asset, user=None, notes=''):
    active = asset.active_assignment
    if active:
        target = active.assigned_to
        with transaction.atomic():
            active.is_active = False
            active.checked_in_at = timezone.now()
            active.checked_in_by = user
            if notes:
                active.notes = (active.notes + '\n' + notes).strip()
            active.save()

            available_status = StatusLabel.objects.filter(type='deployable').first()
            if available_status:
                asset.status = available_status
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from {target}"
            asset.save(update_fields=['status'])

            ActivityLog.objects.create(
                asset=asset,
                action='checked_in',
                user=user,
                notes=f"Checked in from {target}. {notes}".strip()
            )

            return f"Checked in from: {target}"
    elif asset.location:
        with transaction.atomic():
            checked_in_from = asset.location
            asset.location = None
            available_status = StatusLabel.objects.filter(type='deployable').first()
            if available_status:
                asset.status = available_status
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from Location: {checked_in_from}"
            asset.save(update_fields=['status', 'location'])
            return f"Checked in from Location: {checked_in_from}"
    else:
        return None


def checkout_accessory(accessory, qty, holder=None, location=None, user=None, notes="", source_location=None):
    if not accessory.allow_overallocate and accessory.available < qty:
        raise ValidationError("No stock available for checkout.")
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")
    if source_location:
        from inventory.models import AccessoryStock
        loc_stock = AccessoryStock.objects.filter(
            accessory=accessory, location=source_location
        ).aggregate(qty=Sum('qty'))['qty'] or 0
        if not accessory.allow_overallocate and loc_stock < qty:
            raise ValidationError(
                f"Insufficient stock at {source_location}. Available: {loc_stock}, Requested: {qty}"
            )

    with transaction.atomic():
        assignment = AccessoryAssignment.objects.create(
            accessory=accessory,
            assigned_holder=holder,
            assigned_location=location,
            from_location=source_location,
            qty=qty,
            notes=notes
        )
    return assignment


def checkin_accessory(assignment_pk, user=None):
    assignment = get_object_or_404(AccessoryAssignment, pk=assignment_pk)
    accessory = assignment.accessory
    qty = assignment.qty
    recipient = assignment.assigned_holder or assignment.assigned_location

    assignment.delete()
    return accessory, qty, recipient


def checkout_consumable(consumable, qty, holder=None, location=None, user=None, notes="", source_location=None):
    if not consumable.allow_overallocate and consumable.available < qty:
        raise ValidationError("No stock available for consumption checkout.")
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")
    if source_location:
        from inventory.models import ConsumableStock
        loc_stock = ConsumableStock.objects.filter(
            consumable=consumable, location=source_location
        ).aggregate(qty=Sum('qty'))['qty'] or 0
        if not consumable.allow_overallocate and loc_stock < qty:
            raise ValidationError(
                f"Insufficient stock at {source_location}. Available: {loc_stock}, Requested: {qty}"
            )

    with transaction.atomic():
        assignment = ConsumableAssignment.objects.create(
            consumable=consumable,
            assigned_holder=holder,
            assigned_location=location,
            from_location=source_location,
            qty=qty,
            notes=notes
        )
    return assignment


def checkout_kit(kit, holder=None, location=None, user=None, notes="", source_location=None):
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")

    in_use_status = StatusLabel.objects.filter(type='deployed').first()
    if not in_use_status:
        raise ValidationError("No Status Label with type 'Deployed' exists. Please configure one.")

    with transaction.atomic():
        allocated_assets = []

        for item in kit.items.all():
            if item.asset_type:
                asset = Asset.objects.filter(asset_type=item.asset_type, status__type='deployable').first()
                if not asset:
                    raise ValidationError(f"No available assets of type '{item.asset_type}' in stock.")
                allocated_assets.append(asset)
            elif item.accessory:
                rem = item.accessory.available
                if not item.accessory.allow_overallocate and rem < item.qty:
                    raise ValidationError(f"Insufficient stock for accessory '{item.accessory}'. Required: {item.qty}, Available: {rem}")
            elif item.license:
                rem = item.license.available_seats
                if rem < 1:
                    raise ValidationError(f"No available seats for software license '{item.license}'.")

        for item in kit.items.all():
            if item.asset_type:
                asset = Asset.objects.filter(asset_type=item.asset_type, status__type='deployable').first()
                asset.status = in_use_status
                if holder:
                    asset.location = None
                else:
                    asset.location = location

                asset._changelog_action = 'checkout'
                asset._changelog_message = f"Checked out via Kit '{kit.name}'. {notes}"
                asset.save(update_fields=['status', 'location'])

                ActivityLog.objects.create(
                    asset=asset,
                    action='checked_out',
                    user=user,
                    notes=f"Checked out via Kit '{kit.name}'. {notes}"
                )

                target = holder or location
                AssetHolderAssignment.objects.filter(
                    content_type=ContentType.objects.get_for_model(Asset),
                    object_id=asset.pk
                ).delete()

                AssetAssignment.objects.create(
                    asset=asset,
                    assigned_to=target,
                    checked_out_by=user,
                    notes=f"Checked out via Kit '{kit.name}'. {notes}"
                )

            elif item.accessory:
                AccessoryAssignment.objects.create(
                    accessory=item.accessory,
                    assigned_holder=holder,
                    assigned_location=location,
                    from_location=source_location,
                    qty=item.qty,
                    notes=f"Checked out via Kit '{kit.name}'. {notes}"
                )
            elif item.license:
                if holder:
                    LicenseSeatAssignment.objects.create(
                        license=item.license,
                        assigned_holder=holder,
                        notes=f"Checked out via Kit '{kit.name}'. {notes}"
                    )
                elif allocated_assets:
                    LicenseSeatAssignment.objects.create(
                        license=item.license,
                        asset=allocated_assets[0],
                        notes=f"Checked out via Kit '{kit.name}'. {notes}"
                    )
                else:
                    raise ValidationError(f"License seat for '{item.license.name}' must be assigned to either a Holder or an Asset.")
