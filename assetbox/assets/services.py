from django.db import transaction
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404
from .models import Asset, StatusLabel, CustodyReceipt, AccessoryAssignment, ConsumableAssignment, Kit, ActivityLog
from organization.models import AssetHolderAssignment, AssetHolder, Location
from licenses.models import LicenseSeatAssignment

def checkout_asset(asset, holder=None, location=None, user=None, request=None):
    if not asset.status or asset.status.slug != 'available':
        raise ValidationError("Asset is not available for assignment.")
    
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")
    
    assignee = holder or location
    
    with transaction.atomic():
        if holder:
            asset.location = None
        else:
            asset.location = location
            
        in_use_status = StatusLabel.objects.filter(slug='in-use').first()
        if in_use_status:
            asset.status = in_use_status
            
        asset._changelog_action = 'checkout'
        asset._changelog_message = f"Checked out to {'Holder' if holder else 'Location'}: {assignee}"
        asset.save(update_fields=['status', 'location'])
        
        if holder:
            AssetHolderAssignment.objects.update_or_create(
                content_type=ContentType.objects.get_for_model(Asset),
                object_id=asset.pk,
                defaults={
                    'asset_holder': holder,
                }
            )
        else:
            AssetHolderAssignment.objects.filter(
                content_type=ContentType.objects.get_for_model(Asset),
                object_id=asset.pk
            ).delete()
            
        if holder:
            category = asset.asset_type.category if asset.asset_type else None
            if category and category.require_acceptance:
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
    return assignee


def checkin_asset(asset, user=None):
    assignment = AssetHolderAssignment.objects.filter(
        content_type=ContentType.objects.get_for_model(Asset),
        object_id=asset.pk
    ).select_related('asset_holder').first()
    
    if assignment:
        with transaction.atomic():
            checked_in_from = assignment.asset_holder
            from_str = str(checked_in_from) if checked_in_from else 'N/A'
            assignment.delete()
            available_status = StatusLabel.objects.filter(slug='available').first()
            if available_status:
                asset.status = available_status
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from Asset Holder: {from_str}"
            asset.save()
        return f"Checked in from Asset Holder: {from_str}"
    elif asset.location:
        with transaction.atomic():
            checked_in_from = asset.location
            from_str = str(checked_in_from) if checked_in_from else 'N/A'
            asset.location = None
            available_status = StatusLabel.objects.filter(slug='available').first()
            if available_status:
                asset.status = available_status
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from Location: {from_str}"
            asset.save()
        return f"Checked in from Location: {from_str}"
    else:
        return None


def checkout_accessory(accessory, qty, holder=None, location=None, user=None, notes=""):
    if not accessory.allow_overallocate and accessory.remaining_qty < qty:
        raise ValidationError("No stock available for checkout.")
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")
        
    assignment = AccessoryAssignment.objects.create(
        accessory=accessory,
        assigned_holder=holder,
        assigned_location=location,
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


def checkout_consumable(consumable, qty, holder=None, location=None, user=None, notes=""):
    if not consumable.allow_overallocate and consumable.remaining_qty < qty:
        raise ValidationError("No stock available for consumption checkout.")
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")
        
    assignment = ConsumableAssignment.objects.create(
        consumable=consumable,
        assigned_holder=holder,
        assigned_location=location,
        qty=qty,
        notes=notes
    )
    return assignment


def checkout_kit(kit, holder=None, location=None, user=None, notes=""):
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")
        
    in_use_status = StatusLabel.objects.filter(slug='in-use').first()
    if not in_use_status:
        raise ValidationError("The 'in-use' Status Label does not exist. Please configure it.")
        
    with transaction.atomic():
        allocated_assets = []
        
        # Verification Pass
        for item in kit.items.all():
            if item.asset_type:
                asset = Asset.objects.filter(asset_type=item.asset_type, status__slug='available').first()
                if not asset:
                    raise ValidationError(f"No available assets of type '{item.asset_type}' in stock.")
                allocated_assets.append(asset)
            elif item.accessory:
                rem = item.accessory.remaining_qty
                if not item.accessory.allow_overallocate and rem < item.qty:
                    raise ValidationError(f"Insufficient stock for accessory '{item.accessory}'. Required: {item.qty}, Available: {rem}")
            elif item.license:
                rem = item.license.available_seats
                if rem < 1:
                    raise ValidationError(f"No available seats for software license '{item.license}'.")
                    
        # Execution Pass
        for item in kit.items.all():
            if item.asset_type:
                asset = Asset.objects.filter(asset_type=item.asset_type, status__slug='available').first()
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
                
                if holder:
                    AssetHolderAssignment.objects.update_or_create(
                        content_type=ContentType.objects.get_for_model(Asset),
                        object_id=asset.pk,
                        defaults={'asset_holder': holder}
                    )
                else:
                    AssetHolderAssignment.objects.filter(
                        content_type=ContentType.objects.get_for_model(Asset),
                        object_id=asset.pk
                    ).delete()
                    
            elif item.accessory:
                AccessoryAssignment.objects.create(
                    accessory=item.accessory,
                    assigned_holder=holder,
                    assigned_location=location,
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
