import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db import DatabaseError, transaction
from django_q.tasks import async_task

from assets.models import AssetRequest, AssetAssignment
from core.events import dispatch_event

logger = logging.getLogger(__name__)

@receiver(post_save, sender=AssetAssignment)
def on_asset_assignment_save(sender, instance, created, **kwargs):
    try:
        if created:
            transaction.on_commit(lambda: dispatch_event(sender, instance, action='checkout'))
        elif not instance.is_active and instance.checked_in_at:
            transaction.on_commit(lambda: dispatch_event(sender, instance, action='checkin'))
    except DatabaseError as e:
        logger.exception("Database error occurred while processing asset assignment event: %s", e)
    except Exception as e:
        logger.exception("Unexpected error occurred while processing asset assignment event: %s", e)


@receiver(post_save, sender=AssetRequest)
def on_asset_request_save(sender, instance, created, **kwargs):
    try:
        if created:
            transaction.on_commit(lambda: dispatch_event(sender, instance, action='create'))
            
            # Only notify admins for parent requests or standalone requests, avoiding N+1 queries
            if instance.parent is None:
                request_id = instance.pk
                transaction.on_commit(lambda: async_task('assets.tasks.notify_new_request_task', request_id))
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
        assignee = instance.assigned_target
        
        if isinstance(assignee, AssetHolder) and assignee.user:
            user = assignee.user
            
            # Identify any matching pending/approved/procurement requests
            matching_requests = AssetRequest.objects.filter(
                requester=user,
                status__in=[AssetRequest.STATUS_PENDING, AssetRequest.STATUS_APPROVED, AssetRequest.STATUS_PROCUREMENT]
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
