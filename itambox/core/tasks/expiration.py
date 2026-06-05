import logging
from django.utils import timezone

from core.models import Job, Notification

logger = logging.getLogger(__name__)

def nightly_expiration_check_task():
    """
    Scheduled cron-like background task scanning database subscriptions,
    assets warranties, and EOL plans. Generates alerts and logs actions.
    """
    logger.info("Executing nightly expiration and warranty check...")
    job = Job.objects.create(
        name="Scheduled Nightly Expiration & Warranty Check",
        status=Job.STATUS_RUNNING,
        started=timezone.now()
    )
    job.append_log("Starting scheduled asset sweeps...")

    try:
        from subscriptions.models import Subscription
        from assets.models import Asset
        
        now = timezone.now()
        thirty_days_later = now + timezone.timedelta(days=30)
        
        # 1. Expiring Subscriptions (within 30 days)
        expiring_subs = Subscription.objects.filter(
            renewal_date__range=[now, thirty_days_later],
            status='active'
        )
        job.append_log(f"Found {expiring_subs.count()} active subscription(s) expiring within 30 days.")
        
        notifications = []
        for sub in expiring_subs:
            notifications.append(Notification(
                user=None,
                subject="Subscription Renewal Due",
                message=f"Subscription '{sub.name}' is due to renew on {sub.renewal_date:%Y-%m-%d}. Scoped cost: {sub.renewal_cost}.",
                level=Notification.LEVEL_WARNING,
                target_url=sub.get_absolute_url()
            ))
            job.append_log(f" - Added renewal reminder to queue for: {sub.name}")

        # 2. Expiring Warranties (within 30 days)
        expiring_warranties = Asset.objects.filter(
            warranty_expiration__range=[now.date(), thirty_days_later.date()]
        )
        warranty_alert_count = expiring_warranties.count()
        for asset in expiring_warranties:
            notifications.append(Notification(
                user=None,
                subject="Hardware Warranty Expiring",
                message=f"Asset {asset.asset_tag} ({asset.name}) warranty expires on {asset.warranty_expiration:%Y-%m-%d}.",
                level=Notification.LEVEL_WARNING,
                target_url=asset.get_absolute_url()
            ))
            job.append_log(f" - Added warranty alert to queue for: {asset.asset_tag}")

        if notifications:
            Notification.objects.bulk_create(notifications)
            job.append_log(f"Bulk created {len(notifications)} notifications.")

        job.append_log(f"Generated {warranty_alert_count} hardware warranty alert(s).")
        job.mark_completed(result={
            'expiring_subscriptions': expiring_subs.count(),
            'expiring_warranties': warranty_alert_count
        })

    except Exception as e:
        logger.exception("Exception during scheduled nightly checks")
        job.mark_failed(str(e))
