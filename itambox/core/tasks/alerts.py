import logging
from django.utils import timezone

from core.models import AlertRule, AlertLog, NotificationChannel
from core.events import send_notification

logger = logging.getLogger(__name__)

def evaluate_alert_rules_task():
    """
    Scheduled cron task scanning database configurations for active thresholds,
    creating history logs, and triggering multi-channel notifications.
    """
    from core.models import AlertRule, AlertLog, NotificationChannel
    from core.events import send_notification
    from django.db.models import Sum, Q, Subquery, OuterRef
    from django.db.models.functions import Coalesce
    from django.contrib.contenttypes.models import ContentType
    
    active_rules = AlertRule.objects.filter(is_active=True).select_related('tenant')
    logger.info(f"Evaluating {active_rules.count()} active alert rules...")
    
    today = timezone.now().date()
    alerts_triggered_count = 0
    
    # Pre-fetch all active/acknowledged warning logs in a single query to eliminate N+1 lookup check loop
    active_logs = set(
        AlertLog.objects.filter(
            status__in=[AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED]
        ).values_list('rule_id', 'content_type_id', 'object_id')
    )
    
    for rule in active_rules:
        logger.info(f"Evaluating alert rule: {rule.name} (Type: {rule.alert_type}, Threshold: {rule.threshold_value})")
        
        # Bind thread-local active tenant context for rule evaluation
        from core.managers import set_current_tenant
        set_current_tenant(rule.tenant)
        
        matches = []
        
        try:
            if rule.alert_type == AlertRule.ALERT_TYPE_LOW_STOCK:
                from inventory.models import Accessory, Consumable, AccessoryStock, AccessoryAssignment, ConsumableStock, ConsumableAssignment, Component, ComponentStock, ComponentAllocation
                
                # --- ACCESSORIES ---
                # Subquery to aggregate stocks
                acc_stocks_sub = AccessoryStock.objects.filter(accessory=OuterRef('pk'))
                if rule.tenant:
                    acc_stocks_sub = acc_stocks_sub.filter(location__tenant=rule.tenant)
                acc_stocks_sum = Subquery(
                    acc_stocks_sub.values('accessory').annotate(total=Sum('qty')).values('total')
                )
                
                # Subquery to aggregate assignments (undeducted qty)
                acc_assigns_sub = AccessoryAssignment.objects.filter(
                    accessory=OuterRef('pk'),
                    from_location__isnull=True
                )
                acc_assigns_sum = Subquery(
                    acc_assigns_sub.values('accessory').annotate(total=Sum('qty')).values('total')
                )
                
                acc_qs = Accessory.objects.filter(deleted_at__isnull=True).annotate(
                    annotated_total_stock=Coalesce(acc_stocks_sum, 0),
                    annotated_undeducted_qty=Coalesce(acc_assigns_sum, 0)
                )
                if rule.tenant:
                    acc_qs = acc_qs.filter(tenant=rule.tenant)
                
                for acc in acc_qs:
                    total_stock = acc.annotated_total_stock
                    undeducted = acc.annotated_undeducted_qty
                    available = max(0, total_stock - undeducted)
                    
                    threshold = acc.min_qty if (acc.min_qty and acc.min_qty > 0) else rule.threshold_value
                    if available <= threshold:
                        matches.append({
                            'obj': acc,
                            'tenant': acc.tenant,
                            'subject': f"Low Stock: {acc.name}",
                            'message': f"Accessory '{acc.name}' available stock is {available}, which is at or below the safety alert limit of {threshold} units."
                        })
                
                # --- CONSUMABLES ---
                con_stocks_sub = ConsumableStock.objects.filter(consumable=OuterRef('pk'))
                if rule.tenant:
                    con_stocks_sub = con_stocks_sub.filter(location__tenant=rule.tenant)
                con_stocks_sum = Subquery(
                    con_stocks_sub.values('consumable').annotate(total=Sum('qty')).values('total')
                )
                
                con_consumptions_sub = ConsumableAssignment.objects.filter(
                    consumable=OuterRef('pk'),
                    from_location__isnull=True
                )
                con_consumptions_sum = Subquery(
                    con_consumptions_sub.values('consumable').annotate(total=Sum('qty')).values('total')
                )
                
                con_qs = Consumable.objects.filter(deleted_at__isnull=True).annotate(
                    annotated_total_stock=Coalesce(con_stocks_sum, 0),
                    annotated_undeducted_qty=Coalesce(con_consumptions_sum, 0)
                )
                if rule.tenant:
                    con_qs = con_qs.filter(tenant=rule.tenant)
                
                for con in con_qs:
                    total_stock = con.annotated_total_stock
                    undeducted = con.annotated_undeducted_qty
                    available = max(0, total_stock - undeducted)
                    
                    threshold = con.min_qty if (con.min_qty and con.min_qty > 0) else rule.threshold_value
                    if available <= threshold:
                        matches.append({
                            'obj': con,
                            'tenant': con.tenant,
                            'subject': f"Low Stock: {con.name}",
                            'message': f"Consumable '{con.name}' available stock is {available}, which is at or below the safety alert limit of {threshold} units."
                        })
                        
                # --- COMPONENTS ---
                comp_stocks_sub = ComponentStock.objects.filter(component=OuterRef('pk'))
                if rule.tenant:
                    comp_stocks_sub = comp_stocks_sub.filter(location__tenant=rule.tenant)
                comp_stocks_sum = Subquery(
                    comp_stocks_sub.values('component').annotate(total=Sum('qty')).values('total')
                )
                
                comp_allocs_sub = ComponentAllocation.objects.filter(
                    component=OuterRef('pk'),
                    deleted_at__isnull=True
                )
                if rule.tenant:
                    comp_allocs_sub = comp_allocs_sub.filter(assigned_asset__tenant=rule.tenant)
                comp_allocs_sum = Subquery(
                    comp_allocs_sub.values('component').annotate(total=Sum('qty')).values('total')
                )
                
                comp_qs = Component.objects.filter(deleted_at__isnull=True).annotate(
                    annotated_total_stock=Coalesce(comp_stocks_sum, 0),
                    annotated_allocated_stock=Coalesce(comp_allocs_sum, 0)
                )
                if rule.tenant:
                    comp_qs = comp_qs.filter(tenant=rule.tenant)
                
                for comp in comp_qs:
                    total_stock = comp.annotated_total_stock
                    allocated = comp.annotated_allocated_stock
                    available = total_stock - allocated
                    
                    threshold = comp.min_qty if (comp.min_qty and comp.min_qty > 0) else rule.threshold_value
                    if available <= threshold:
                        matches.append({
                            'obj': comp,
                            'tenant': rule.tenant,
                            'subject': f"Low Stock: {comp.name}",
                            'message': f"Component '{comp.name}' available stock is {available}, which is at or below the safety alert limit of {threshold} units."
                        })
                        
            elif rule.alert_type == AlertRule.ALERT_TYPE_UPCOMING_EOL:
                from assets.models import Asset
                
                deadline = today + timezone.timedelta(days=rule.threshold_value)
                # EOL is purchase_date + eol_months. Filter candidates and check in Python.
                assets = Asset.objects.filter(
                    deleted_at__isnull=True,
                    purchase_date__isnull=False,
                    asset_type__eol_months__gt=0
                ).select_related('asset_type')
                if rule.tenant:
                    assets = assets.filter(tenant=rule.tenant)
                for asset in assets:
                    eol = asset.eol_date
                    if eol and today <= eol <= deadline:
                        days_left = (eol - today).days
                        matches.append({
                            'obj': asset,
                            'tenant': asset.tenant,
                            'subject': f"Upcoming Hardware EOL: {asset.asset_tag}",
                            'message': f"Asset {asset.asset_tag} ({asset.name}) reaches EOL on {eol:%Y-%m-%d} ({days_left} day(s) remaining)."
                        })
                    
            elif rule.alert_type == AlertRule.ALERT_TYPE_LICENSE_EXPIRY:
                from licenses.models import License
                
                deadline = today + timezone.timedelta(days=rule.threshold_value)
                licenses = License.objects.filter(
                    deleted_at__isnull=True,
                    expiration_date__lte=deadline,
                    expiration_date__gte=today
                )
                if rule.tenant:
                    licenses = licenses.filter(tenant=rule.tenant)
                for lic in licenses:
                    days_left = (lic.expiration_date - today).days
                    matches.append({
                        'obj': lic,
                        'tenant': lic.tenant,
                        'subject': f"License Expiring: {lic.name}",
                        'message': f"License '{lic.name}' expires on {lic.expiration_date} ({days_left} day(s) remaining)."
                    })
                    
            elif rule.alert_type == AlertRule.ALERT_TYPE_RENEWAL_DUE:
                from subscriptions.models import Subscription
                
                deadline = today + timezone.timedelta(days=rule.threshold_value)
                subs = Subscription.objects.filter(
                    deleted_at__isnull=True,
                    status='active',
                    renewal_date__lte=deadline,
                    renewal_date__gte=today
                )
                if rule.tenant:
                    subs = subs.filter(tenant=rule.tenant)
                for sub in subs:
                    days_left = (sub.renewal_date - today).days
                    matches.append({
                        'obj': sub,
                        'tenant': sub.tenant,
                        'subject': f"Subscription Renewal Due: {sub.name}",
                        'message': f"Subscription '{sub.name}' ends on {sub.renewal_date} ({days_left} day(s) remaining) and requires renewal validation."
                    })
                    
            for match in matches:
                obj = match['obj']
                ct = ContentType.objects.get_for_model(obj)
                
                # Zero-latency set lookups (O(1)) completely avoiding N+1 lookup checking loop
                existing_alert = (rule.id, ct.id, obj.pk) in active_logs
                
                if not existing_alert:
                    alert_log = AlertLog.objects.create(
                        rule=rule,
                        subject=match['subject'],
                        message=match['message'],
                        content_type=ct,
                        object_id=obj.pk,
                        tenant=match.get('tenant')
                    )
                    
                    channels = rule.channels.all()
                    if not channels.exists():
                        if match.get('tenant'):
                            channels = NotificationChannel.objects.filter(tenant=match['tenant'], enabled=True)
                        else:
                            channels = NotificationChannel.objects.filter(tenant__isnull=True, enabled=True)
                        
                    for channel in channels:
                        try:
                            from core.events import send_notification_to_channel
                            send_notification_to_channel(channel, match['subject'], match['message'])
                        except Exception as ne:
                            logger.error(f"Failed to dispatch notification to channel {channel}: {str(ne)}")
                        
                    alerts_triggered_count += 1
                    logger.info(f"Triggered AlertLog {alert_log.pk} for '{match['subject']}' on object '{str(obj)}'.")
                    
        finally:
            from core.managers import set_current_tenant, set_current_membership
            set_current_tenant(None)
            set_current_membership(None)
                
    logger.info(f"Alert evaluation complete. Triggered {alerts_triggered_count} fresh alert(s).")
    return alerts_triggered_count
