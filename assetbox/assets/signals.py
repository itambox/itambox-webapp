import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db import DatabaseError
from django.contrib.auth import get_user_model
from assets.models import AssetRequest, AssetAssignment
from inventory.models import ConsumableAssignment
from core.models import Notification
from core.events import dispatch_event

logger = logging.getLogger(__name__)
User = get_user_model()

@receiver(post_save, sender=ConsumableAssignment)
@receiver(post_delete, sender=ConsumableAssignment)
def check_consumable_stock(sender, instance, **kwargs):
    consumable = instance.consumable
    remaining = consumable.available
    min_qty = consumable.min_qty
    
    # Check if stock dips below safety threshold
    if remaining < min_qty:
        # Determine warning level and message
        if remaining <= 0:
            level = Notification.LEVEL_DANGER
            subject = f"Out of Stock: {consumable.name}"
            message = f"The consumable item '{consumable}' is completely OUT OF STOCK! Remaining count is {remaining} (Safety threshold: {min_qty})."
        else:
            level = Notification.LEVEL_WARNING
            subject = f"Low Stock Warning: {consumable.name}"
            message = f"The consumable item '{consumable}' is running low. Remaining count is {remaining} (Safety threshold: {min_qty})."
        
        # Dispatch notification to all administrators and staff members
        admins = User.objects.filter(is_staff=True)
        for admin in admins:
            # Prevent creating duplicate unread warnings for the same consumable
            if not Notification.objects.filter(user=admin, subject=subject, is_read=False).exists():
                Notification.objects.create(
                    user=admin,
                    subject=subject,
                    message=message,
                    level=level,
                    target_url=consumable.get_absolute_url()
                )


@receiver(post_save, sender=AssetAssignment)
def on_asset_assignment_save(sender, instance, created, **kwargs):
    try:
        if created:
            dispatch_event(sender, instance, action='checkout')
        elif not instance.is_active and instance.checked_in_at:
            dispatch_event(sender, instance, action='checkin')
    except DatabaseError as e:
        logger.exception("Database error occurred while processing asset assignment event: %s", e)
    except Exception as e:
        logger.exception("Unexpected error occurred while processing asset assignment event: %s", e)


@receiver(post_save, sender=AssetRequest)
def on_asset_request_save(sender, instance, created, **kwargs):
    try:
        if created:
            dispatch_event(sender, instance, action='create')
            admins = User.objects.filter(is_staff=True)
            for admin in admins:
                Notification.objects.create(
                    user=admin,
                    subject=f"New Asset Request from {instance.requester}",
                    message=f"{instance.requester} has requested {instance}.",
                    level=Notification.LEVEL_INFO,
                    target_url=instance.get_absolute_url()
                )
    except DatabaseError as e:
        logger.exception("Database error occurred while processing asset request notification: %s", e)
    except Exception as e:
        logger.exception("Unexpected error occurred while processing asset request notification: %s", e)


@receiver(post_save, sender=AssetAssignment)
def auto_fulfill_asset_requests(sender, instance, created, **kwargs):
    """
    Listens for new active AssetAssignments and automatically transitions compatible 
    pending/approved AssetRequests for that holder to a 'fulfilled' status.
    """
    if created and instance.is_active:
        from django.db import models
        from django.utils import timezone
        from organization.models import AssetHolder
        
        asset = instance.asset
        assignee = instance.assigned_to
        
        if isinstance(assignee, AssetHolder) and assignee.user:
            user = assignee.user
            
            # Identify any matching pending/approved requests
            matching_requests = AssetRequest.objects.filter(
                requester=user,
                status__in=[AssetRequest.STATUS_PENDING, AssetRequest.STATUS_APPROVED]
            ).filter(
                models.Q(asset=asset) | 
                models.Q(asset_type=asset.asset_type, asset__isnull=True)
            )

            for req in matching_requests:
                req.status = AssetRequest.STATUS_FULFILLED
                req.asset = asset
                req.responded_by = instance.checked_out_by
                req.response_date = timezone.now()
                req.response_notes = f"Automatically fulfilled via assignment checkout transaction ID: {instance.pk}."
                req.save()

