from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.http import HttpRequest
    from organization.models import AssetHolder, Location

from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .choices import StatusTypeChoices
from .models import Asset, StatusLabel, AssetAssignment
from compliance.models import CustodyReceipt
from inventory.models import AccessoryAssignment, ConsumableAssignment
from licenses.models import LicenseSeatAssignment


def checkout_asset(
    asset: Asset,
    holder: AssetHolder | None = None,
    location: Location | None = None,
    asset_target: Asset | None = None,
    user: AbstractBaseUser | None = None,
    request: HttpRequest | None = None,
    expected_checkin: datetime.date | None = None,
    notes: str = '',
    checkout_date: datetime.datetime | None = None,
    status: StatusLabel | None = None
) -> AssetHolder | Location | Asset:
    target = holder or location or asset_target
    if not target:
        raise ValidationError("Either holder, location, or asset must be specified.")

    with transaction.atomic():
        # Lock the asset row to prevent concurrent overallocation or state issues
        asset = Asset.objects.select_for_update().get(pk=asset.pk)

        # Lifecycle guard: assets on order or in repair are not deployable.
        if asset.status and asset.status.type in (
            StatusTypeChoices.IN_REPAIR, StatusTypeChoices.ON_ORDER
        ):
            raise ValidationError(
                f"Cannot check out an asset that is {asset.status.get_type_display()}."
            )

        if asset.active_assignment:
            checkin_asset(asset, user=user, notes='Auto-checkin for reassignment')

        original_status = asset.status

        resolved_status = status
        if not resolved_status:
            resolved_status = StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYED).first()
        if resolved_status:
            asset.status = resolved_status

        if holder:
            asset.location = None
        elif location:
            asset.location = location
        elif asset_target:
            asset.location = asset_target.location

        asset._changelog_action = 'checkout'
        asset._changelog_message = f"Checked out to {target}"
        asset.save(update_fields=['status', 'location'])



        assignment_kwargs = {
            'asset': asset,
            'checked_out_by': user,
            'expected_checkin_date': expected_checkin,
            'notes': notes,
            'pre_checkout_status': original_status,
        }
        if holder:
            assignment_kwargs['assigned_user'] = holder
        elif location:
            assignment_kwargs['assigned_location'] = location
        elif asset_target:
            assignment_kwargs['assigned_asset'] = asset_target

        if checkout_date:
            assignment_kwargs['checked_out_at'] = checkout_date

        assignment = AssetAssignment.objects.create(**assignment_kwargs)

        category = asset.asset_type.category if asset.asset_type else None
        if holder and category:
            from compliance.models import CustodyTemplate
            resolved_template = None

            # Priority 1: Tenant-specific override for the category
            if holder.tenant:
                resolved_template = CustodyTemplate.objects.filter(
                    tenant=holder.tenant,
                    category=category,
                    is_active=True
                ).first()

            # Priority 2: Tenant Group-specific override (if tenant belongs to a group)
            if not resolved_template and holder.tenant and holder.tenant.group:
                resolved_template = CustodyTemplate.objects.filter(
                    tenant_group=holder.tenant.group,
                    category=category,
                    is_active=True
                ).first()

            # Priority 3: Global category template (only if allowed by settings)
            if not resolved_template:
                from django.conf import settings
                if getattr(settings, 'ALLOW_GLOBAL_CUSTODY_TEMPLATES', True):
                    resolved_template = CustodyTemplate.objects.filter(
                        tenant__isnull=True,
                        tenant_group__isnull=True,
                        category=category,
                        is_active=True
                    ).first()

            if resolved_template and resolved_template.require_acceptance:
                receipt_kwargs = {
                    'asset': asset,
                    'holder': holder,
                    'custody_template': resolved_template,
                    'signature_provider': resolved_template.signature_provider,
                    'eula_text': resolved_template.eula_text,
                    'disclaimer': resolved_template.disclaimer,
                    'qms_reference': resolved_template.qms_reference,
                }
                receipt = CustodyReceipt.objects.create(**receipt_kwargs)

                # Send email signature request link if configured
                if resolved_template.email_signature_request and request:
                    try:
                        from core.models import EmailSettings
                        email_config = EmailSettings.load()
                        if email_config and email_config.enabled and email_config.from_address:
                            recipient = holder.email
                            if not recipient:
                                recipient = email_config.test_recipient or email_config.from_address
                            if recipient:
                                from compliance.registry import signature_providers
                                provider = signature_providers.get(receipt.signature_provider or 'local')
                                sign_url = provider.initiate_signature(receipt, request)
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


def checkin_asset(
    asset: Asset,
    user: AbstractBaseUser | None = None,
    notes: str = '',
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
            
            if checkin_date:
                dt = datetime.datetime.combine(checkin_date, datetime.time.min)
                active.checked_in_at = timezone.make_aware(dt)
            else:
                active.checked_in_at = timezone.now()
                
            active.checked_in_by = user
            if notes:
                active.notes = (active.notes + '\n' + notes).strip()
            active.save()

            revert_status = status
            if not revert_status:
                revert_status = active.pre_checkout_status
            if not revert_status:
                revert_status = StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYABLE).first()
            
            if revert_status:
                asset.status = revert_status
            asset.location = location
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from {target}"
            asset.save(update_fields=['status', 'location'])

            return f"Checked in from: {target}"
    elif asset.location:
        with transaction.atomic():
            checked_in_from = asset.location
            revert_status = status
            if not revert_status:
                revert_status = StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYABLE).first()
            if revert_status:
                asset.status = revert_status
            asset.location = location
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from Location: {checked_in_from}"
            asset.save(update_fields=['status', 'location'])
            return f"Checked in from Location: {checked_in_from}"
    else:
        return None


def dispose_asset(
    asset: Asset,
    disposal_method: str,
    disposal_date,
    data_sanitization_method: str = 'none',
    sanitization_certificate: str = '',
    sanitized_by: str = '',
    recipient: str = '',
    proceeds=None,
    currency: str = '',
    weee_compliant: bool = False,
    notes: str = '',
    user=None,
) -> 'AssetDisposal':
    """Record the end-of-life disposal of an asset.

    Creates (or replaces) an ``AssetDisposal`` record, stamps ``disposed_at``
    and ``disposal_value`` on the ``Asset``, and transitions the asset to an
    *archived* ``StatusLabel``.  If no archived label exists the asset status
    is left unchanged and a warning is returned alongside the record.

    The whole operation is wrapped in a database transaction; either
    everything succeeds or nothing is written.
    """
    from assets.models import AssetDisposal  # local import avoids circular at module load

    with transaction.atomic():
        # Lock the asset row to prevent concurrent mutations
        asset = Asset._base_manager.select_for_update().get(pk=asset.pk)

        # Auto-checkin any active assignment before disposal
        if asset.active_assignment:
            checkin_asset(asset, user=user, notes='Auto-checkin for disposal')
            asset.refresh_from_db()

        # Transition to an archived status label (first one found)
        archived_label = StatusLabel.objects.filter(
            type=StatusTypeChoices.ARCHIVED
        ).first()

        # Remove any existing disposal record for this asset (idempotent re-run)
        AssetDisposal.all_objects.filter(asset=asset).delete()

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
        disposal.save()

        # Update the asset: stamp disposal fields and transition status
        asset.disposed_at = timezone.now()
        if proceeds is not None:
            asset.disposal_value = proceeds

        if archived_label:
            asset.status = archived_label

        asset._changelog_action = 'dispose'
        asset._changelog_message = (
            f"Disposed via {disposal.get_disposal_method_display()} "
            f"on {disposal_date}"
        )
        asset.save(update_fields=['disposed_at', 'disposal_value', 'status'])

    return disposal


def checkout_kit(kit, holder=None, location=None, user=None, notes="", source_location=None, request=None, **kwargs):
    if not holder and not location:
        raise ValidationError("Either holder or location must be specified.")

    in_use_status = StatusLabel.objects.filter(type=StatusTypeChoices.DEPLOYED).first()
    if not in_use_status:
        raise ValidationError("No Status Label with type 'Deployed' exists. Please configure one.")

    with transaction.atomic():
        allocated_assets = []
        item_assets_map = {}

        # 1. Lock all resources first to prevent race conditions (TOCTOU)
        for item in kit.items.select_related('asset_type', 'accessory', 'license', 'consumable').all():
            if item.asset_type:
                # Lock a deployable asset immediately
                asset = Asset.objects.filter(
                    asset_type=item.asset_type,
                    status__type=StatusTypeChoices.DEPLOYABLE
                ).select_for_update().first()
                if not asset:
                    raise ValidationError(f"No available assets of type '{item.asset_type}' in stock.")
                allocated_assets.append(asset)
                item_assets_map[item.pk] = asset
            elif item.accessory:
                # Lock accessory stock pool
                acc = item.accessory.__class__.objects.select_for_update().get(pk=item.accessory.pk)
                rem = acc.available
                if not acc.allow_overallocate and rem < item.qty:
                    raise ValidationError(f"Insufficient stock for accessory '{acc}'. Required: {item.qty}, Available: {rem}")
            elif item.license:
                # Lock license seat pool
                lic = item.license.__class__.objects.select_for_update().get(pk=item.license.pk)
                rem = lic.available_seats
                if rem < 1:
                    raise ValidationError(f"No available seats for software license '{lic}'.")
            elif item.consumable:
                # Lock consumable stock pool
                con = item.consumable.__class__.objects.select_for_update().get(pk=item.consumable.pk)
                rem = con.available
                if not con.allow_overallocate and rem < item.qty:
                    raise ValidationError(f"Insufficient stock for consumable '{con}'. Required: {item.qty}, Available: {rem}")

        # 2. Perform allocations safely under active locks
        for item in kit.items.all():
            if item.asset_type:
                asset = item_assets_map[item.pk]
                asset.status = in_use_status
                if holder:
                    asset.location = None
                else:
                    asset.location = location

                asset._changelog_action = 'checkout'
                asset._changelog_message = f"Checked out via Kit '{kit.name}'. {notes}"
                asset.save(update_fields=['status', 'location'])

                target = holder or location


                assignment_kwargs = {
                    'asset': asset,
                    'checked_out_by': user,
                    'notes': f"Checked out via Kit '{kit.name}'. {notes}"
                }
                if holder:
                    assignment_kwargs['assigned_user'] = holder
                elif location:
                    assignment_kwargs['assigned_location'] = location
                    
                AssetAssignment.objects.create(**assignment_kwargs)

            elif item.accessory:
                AccessoryAssignment.objects.create(
                    accessory=item.accessory,
                    assigned_holder=holder,
                    assigned_location=location,
                    from_location=source_location,
                    qty=item.qty,
                    notes=f"Checked out via Kit '{kit.name}'. {notes}"
                )
            elif item.consumable:
                ConsumableAssignment.objects.create(
                    consumable=item.consumable,
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
