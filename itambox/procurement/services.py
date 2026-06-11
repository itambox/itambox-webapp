from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from assets.models import Asset, StatusLabel, AssetRequest
from inventory.models import ComponentStock, AccessoryStock, ConsumableStock
from procurement.models import PurchaseOrder, FulfillmentLink

@transaction.atomic
def receive_purchase_order(po, line_quantities, asset_details=None):
    """
    line_quantities: dict of {line_id (int): qty_to_receive (int)}
    asset_details: list of dicts [{'line_id': int, 'serial_number': str, 'asset_tag': str, 'name': str}]
    """
    if po.status not in [PurchaseOrder.STATUS_ORDERED, PurchaseOrder.STATUS_PARTIAL]:
        raise ValidationError(f"Cannot receive stock on a purchase order in '{po.get_status_display()}' status. It must be Ordered or Partially Received.")

    any_outstanding = False
    
    # Pre-fetch deployable status label
    deployable_status = StatusLabel.objects.filter(type='deployable').first()
    if not deployable_status:
        raise ValidationError("Deployable status label does not exist in the database.")

    # Group asset details by line_id for quick lookup
    details_by_line = {}
    if asset_details:
        for detail in asset_details:
            if not detail or 'line_id' not in detail:
                continue
            lid = int(detail['line_id'])
            details_by_line.setdefault(lid, []).append(detail)

    for line in po.lines.select_for_update().all():
        qty = line_quantities.get(line.pk, 0)
        if qty <= 0:
            if line.qty_outstanding > 0:
                any_outstanding = True
            continue
            
        if qty > line.qty_outstanding:
            raise ValidationError(f"Cannot receive {qty} for line {line.pk} — only {line.qty_outstanding} outstanding.")
        
        if line.asset_type:
            # Get details for this line
            details = details_by_line.get(line.pk, [])
            
            # Find any linked AssetRequests via FulfillmentLink
            # We want to allocate the created assets to these requests
            # We only select requests that are currently in 'procurement' status
            linked_requests = list(
                AssetRequest.objects.filter(
                    fulfillment_links__purchase_order_line=line,
                    status=AssetRequest.STATUS_PROCUREMENT
                ).select_for_update().order_by('request_date')
            )
            
            req_idx = 0
            for i in range(qty):
                # Get detail or empty dict
                detail = details[i] if i < len(details) else {}
                
                asset_name = detail.get('name') or str(line.asset_type)
                if not asset_name:
                    asset_name = f"{line.asset_type.manufacturer.name} {line.asset_type.model}"
                
                # Create Asset
                asset = Asset.objects.create(
                    name=asset_name.strip(),
                    asset_type=line.asset_type,
                    serial_number=detail.get('serial_number', '').strip() or '',
                    asset_tag=detail.get('asset_tag', '').strip() or '',  # If empty, save() auto-generates
                    status=deployable_status,
                    location=po.destination_location,
                    supplier=po.supplier,
                    purchase_cost=line.unit_price,
                    purchase_date=timezone.now().date(),
                    order_number=po.order_number,
                    tenant=po.tenant,
                    purchase_order_line=line
                )
                
                # Try to allocate this asset to an outstanding request
                if req_idx < len(linked_requests):
                    req = linked_requests[req_idx]
                    req.asset = asset
                    req.status = AssetRequest.STATUS_APPROVED
                    req.save()
                    req_idx += 1
                    
        elif line.component:
            stock, _ = ComponentStock.objects.get_or_create(
                component=line.component,
                location=po.destination_location,
                defaults={'qty': 0}
            )
            stock.qty += qty
            stock.save()
            
            # Transition linked component requests to approved
            linked_requests = list(
                AssetRequest.objects.filter(
                    fulfillment_links__purchase_order_line=line,
                    status=AssetRequest.STATUS_PROCUREMENT
                ).select_for_update().order_by('request_date')
            )
            for req in linked_requests:
                req.status = AssetRequest.STATUS_APPROVED
                req.save()
                
        elif line.accessory:
            stock, _ = AccessoryStock.objects.get_or_create(
                accessory=line.accessory,
                location=po.destination_location,
                defaults={'qty': 0}
            )
            stock.qty += qty
            stock.save()
            
            # Transition linked accessory requests to approved
            linked_requests = list(
                AssetRequest.objects.filter(
                    fulfillment_links__purchase_order_line=line,
                    status=AssetRequest.STATUS_PROCUREMENT
                ).select_for_update().order_by('request_date')
            )
            for req in linked_requests:
                req.status = AssetRequest.STATUS_APPROVED
                req.save()
                
        elif line.consumable:
            stock, _ = ConsumableStock.objects.get_or_create(
                consumable=line.consumable,
                location=po.destination_location,
                defaults={'qty': 0}
            )
            stock.qty += qty
            stock.save()
            
            # Transition linked consumable requests to approved
            linked_requests = list(
                AssetRequest.objects.filter(
                    fulfillment_links__purchase_order_line=line,
                    status=AssetRequest.STATUS_PROCUREMENT
                ).select_for_update().order_by('request_date')
            )
            for req in linked_requests:
                req.status = AssetRequest.STATUS_APPROVED
                req.save()
        
        elif line.license:
            # Increment qty_received and transition linked requests
            linked_requests = list(
                AssetRequest.objects.filter(
                    fulfillment_links__purchase_order_line=line,
                    status=AssetRequest.STATUS_PROCUREMENT
                ).select_for_update().order_by('request_date')
            )
            for req in linked_requests:
                req.status = AssetRequest.STATUS_APPROVED
                req.save()
        
        line.qty_received += qty
        line.save(update_fields=['qty_received'])
        
        if line.qty_outstanding > 0:
            any_outstanding = True
            
    # Set correct PO status
    if any_outstanding:
        po.status = PurchaseOrder.STATUS_PARTIAL
    else:
        po.status = PurchaseOrder.STATUS_RECEIVED
    po.save(update_fields=['status'])


@transaction.atomic
def approve_purchase_order(po, user=None, request=None):
    """Transition PO from draft to approved status."""
    if po.status != PurchaseOrder.STATUS_DRAFT:
        raise ValidationError(f"Cannot approve a purchase order in '{po.get_status_display()}' status.")
    if not po.lines.exists():
        raise ValidationError("Cannot approve a purchase order with no line items.")
    po.status = PurchaseOrder.STATUS_APPROVED
    po.save(update_fields=['status'])
    return {"message": f"Purchase Order {po.order_number} has been approved."}


@transaction.atomic
def order_purchase_order(po, user=None, request=None):
    """Transition PO from approved to ordered status."""
    if po.status != PurchaseOrder.STATUS_APPROVED:
        raise ValidationError(f"Cannot mark a purchase order as ordered when in '{po.get_status_display()}' status. It must be Approved first.")
    po.status = PurchaseOrder.STATUS_ORDERED
    if not po.order_date:
        po.order_date = timezone.now().date()
    po.save(update_fields=['status', 'order_date'])
    return {"message": f"Purchase Order {po.order_number} marked as Ordered."}


@transaction.atomic
def cancel_purchase_order(po, user=None, request=None):
    """Transition PO from draft, approved, or ordered to cancelled status."""
    allowed_statuses = [PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_APPROVED, PurchaseOrder.STATUS_ORDERED]
    if po.status not in allowed_statuses:
        raise ValidationError(f"Cannot cancel a purchase order in '{po.get_status_display()}' status.")
    
    # Revert linked AssetRequests back to Approved and delete FulfillmentLinks
    for line in po.lines.all():
        links = FulfillmentLink.objects.filter(purchase_order_line=line)
        for link in links:
            req = link.asset_request
            if req.status == AssetRequest.STATUS_PROCUREMENT:
                req.status = AssetRequest.STATUS_APPROVED
                req.save(update_fields=['status'])
            link.delete()

    po.status = PurchaseOrder.STATUS_CANCELLED
    po.save(update_fields=['status'])
    return {"message": f"Purchase Order {po.order_number} cancelled. Linked asset requests reverted to Approved status."}


@transaction.atomic
def reopen_purchase_order(po, user=None, request=None):
    """Transition PO from cancelled back to draft status."""
    if po.status != PurchaseOrder.STATUS_CANCELLED:
        raise ValidationError(f"Cannot reopen a purchase order in '{po.get_status_display()}' status.")
    
    po.status = PurchaseOrder.STATUS_DRAFT
    po.save(update_fields=['status'])
    return {"message": f"Purchase Order {po.order_number} has been reopened and set to Draft."}
