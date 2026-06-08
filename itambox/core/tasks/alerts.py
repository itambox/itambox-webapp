import logging
from django.utils import timezone

from core.models import AlertRule, AlertLog, NotificationChannel
from core.events import send_notification_to_channel
from core.tasks.context import TaskContext

logger = logging.getLogger(__name__)


def evaluate_alert_rules_task():
    """
    Scheduled daily task: evaluate all active AlertRules, create AlertLogs for
    new matches, auto-resolve logs whose conditions have cleared, and dispatch
    channel notifications.
    """
    from django.contrib.contenttypes.models import ContentType
    from core.managers import set_current_tenant, set_current_membership

    # Clear any ambient tenant from a calling request context.
    set_current_tenant(None)
    set_current_membership(None)

    active_rules = AlertRule.objects.filter(is_active=True).select_related('tenant')
    logger.info("Evaluating %d active alert rules...", active_rules.count())

    today = timezone.now().date()
    alerts_triggered_count = 0

    # Pre-fetch active/acknowledged log keys for O(1) dedup lookup.
    active_logs = set(
        AlertLog.objects.filter(
            status__in=[AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED]
        ).values_list('rule_id', 'content_type_id', 'object_id')
    )

    for rule in active_rules:
        logger.info(
            "Evaluating rule: %s (type=%s, threshold=%s)",
            rule.name, rule.alert_type, rule.threshold_value,
        )

        # Use a system-level TaskContext; no specific user_id for scheduled tasks.
        with TaskContext(tenant_id=rule.tenant_id):
            matched_keys = set()  # (content_type_id, object_id) for this rule run
            matches = []

            try:
                matches = _collect_matches(rule, today)
            except Exception:
                logger.exception("Error collecting matches for rule %s", rule.name)
                continue

            for match in matches:
                obj = match['obj']
                ct = ContentType.objects.get_for_model(obj)
                key = (rule.id, ct.id, obj.pk)
                matched_keys.add((ct.id, obj.pk))

                if key not in active_logs:
                    alert_log = AlertLog.objects.create(
                        rule=rule,
                        subject=match['subject'],
                        message=match['message'],
                        severity=rule.severity,
                        content_type=ct,
                        object_id=obj.pk,
                        tenant=match.get('tenant'),
                    )

                    delivery = _dispatch_channels(rule, match, alert_log)
                    alert_log.delivery_status = delivery
                    alert_log.save(update_fields=['delivery_status'])

                    active_logs.add(key)
                    alerts_triggered_count += 1
                    logger.info(
                        "Triggered AlertLog %s for '%s' on '%s'.",
                        alert_log.pk, match['subject'], obj,
                    )

            # Auto-resolve logs whose conditions have cleared.
            _auto_resolve_cleared(rule, matched_keys)

    logger.info(
        "Alert evaluation complete. Triggered %d fresh alert(s).",
        alerts_triggered_count,
    )
    return alerts_triggered_count


def _dispatch_channels(rule, match, alert_log):
    """Send notifications to all effective channels; return per-channel delivery dict."""
    channels = rule.channels.all()
    if not channels.exists():
        tenant = match.get('tenant')
        if tenant:
            channels = NotificationChannel.objects.filter(tenant=tenant, enabled=True)
        else:
            channels = NotificationChannel.objects.filter(tenant__isnull=True, enabled=True)

    delivery = {}
    for channel in channels:
        try:
            ok = send_notification_to_channel(channel, match['subject'], match['message'])
            delivery[str(channel.pk)] = 'ok' if ok else 'failed'
            if not ok:
                logger.warning(
                    "Channel %s (%s) returned failure for alert '%s'.",
                    channel.name, channel.channel_type, match['subject'],
                )
        except Exception as exc:
            delivery[str(channel.pk)] = f'error: {exc}'
            logger.error(
                "Exception dispatching to channel %s for alert '%s': %s",
                channel, match['subject'], exc,
            )
    return delivery


def _auto_resolve_cleared(rule, matched_keys):
    """
    Flip active/acknowledged logs to resolved when their condition no longer
    matches, so the rule can re-fire if the object dips below threshold again.
    """
    stale_qs = AlertLog.objects.filter(
        rule=rule,
        status__in=[AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED],
    )
    resolved_count = 0
    for log in stale_qs:
        if (log.content_type_id, log.object_id) not in matched_keys:
            log.status = AlertLog.STATUS_RESOLVED
            log.resolved_at = timezone.now()
            log.save(update_fields=['status', 'resolved_at'])
            resolved_count += 1

    if resolved_count:
        logger.info("Auto-resolved %d cleared alert(s) for rule '%s'.", resolved_count, rule.name)


def _collect_matches(rule, today):
    """Return list of match dicts for the given rule and date."""
    from django.db.models import Sum, Q, Subquery, OuterRef
    from django.db.models.functions import Coalesce

    matches = []

    if rule.alert_type == AlertRule.ALERT_TYPE_LOW_STOCK:
        matches.extend(_match_low_stock(rule))

    elif rule.alert_type == AlertRule.ALERT_TYPE_UPCOMING_EOL:
        matches.extend(_match_upcoming_eol(rule, today))

    elif rule.alert_type == AlertRule.ALERT_TYPE_LICENSE_EXPIRY:
        matches.extend(_match_license_expiry(rule, today))

    elif rule.alert_type == AlertRule.ALERT_TYPE_RENEWAL_DUE:
        matches.extend(_match_renewal_due(rule, today))

    elif rule.alert_type == AlertRule.ALERT_TYPE_WARRANTY_EXPIRY:
        matches.extend(_match_warranty_expiry(rule, today))

    elif rule.alert_type == AlertRule.ALERT_TYPE_AUDIT_OVERDUE:
        matches.extend(_match_audit_overdue(rule, today))

    return matches


def _match_low_stock(rule):
    from django.db.models import Sum, Subquery, OuterRef
    from django.db.models.functions import Coalesce
    from inventory.models import (
        Accessory, AccessoryStock, AccessoryAssignment,
        Consumable, ConsumableStock, ConsumableAssignment,
        Component, ComponentStock, ComponentAllocation,
    )

    matches = []

    # --- Accessories ---
    acc_stocks_sub = AccessoryStock.objects.filter(accessory=OuterRef('pk'))
    if rule.tenant:
        acc_stocks_sub = acc_stocks_sub.filter(location__tenant=rule.tenant)
    acc_assigns_sub = AccessoryAssignment.objects.filter(
        accessory=OuterRef('pk'), from_location__isnull=True,
    )
    acc_qs = Accessory.objects.filter(deleted_at__isnull=True).annotate(
        annotated_total_stock=Coalesce(
            Subquery(acc_stocks_sub.values('accessory').annotate(t=Sum('qty')).values('t')), 0
        ),
        annotated_undeducted_qty=Coalesce(
            Subquery(acc_assigns_sub.values('accessory').annotate(t=Sum('qty')).values('t')), 0
        ),
    )
    if rule.tenant:
        acc_qs = acc_qs.filter(tenant=rule.tenant)

    for acc in acc_qs:
        available = max(0, acc.annotated_total_stock - acc.annotated_undeducted_qty)
        threshold = acc.min_qty if (acc.min_qty and acc.min_qty > 0) else rule.threshold_value
        if available <= threshold:
            matches.append({
                'obj': acc, 'tenant': acc.tenant,
                'subject': f"Low Stock: {acc.name}",
                'message': (
                    f"Accessory '{acc.name}' available stock is {available}, "
                    f"at or below the safety limit of {threshold} units."
                ),
            })

    # --- Consumables ---
    con_stocks_sub = ConsumableStock.objects.filter(consumable=OuterRef('pk'))
    if rule.tenant:
        con_stocks_sub = con_stocks_sub.filter(location__tenant=rule.tenant)
    con_assigns_sub = ConsumableAssignment.objects.filter(
        consumable=OuterRef('pk'), from_location__isnull=True,
    )
    con_qs = Consumable.objects.filter(deleted_at__isnull=True).annotate(
        annotated_total_stock=Coalesce(
            Subquery(con_stocks_sub.values('consumable').annotate(t=Sum('qty')).values('t')), 0
        ),
        annotated_undeducted_qty=Coalesce(
            Subquery(con_assigns_sub.values('consumable').annotate(t=Sum('qty')).values('t')), 0
        ),
    )
    if rule.tenant:
        con_qs = con_qs.filter(tenant=rule.tenant)

    for con in con_qs:
        available = max(0, con.annotated_total_stock - con.annotated_undeducted_qty)
        threshold = con.min_qty if (con.min_qty and con.min_qty > 0) else rule.threshold_value
        if available <= threshold:
            matches.append({
                'obj': con, 'tenant': con.tenant,
                'subject': f"Low Stock: {con.name}",
                'message': (
                    f"Consumable '{con.name}' available stock is {available}, "
                    f"at or below the safety limit of {threshold} units."
                ),
            })

    # --- Components ---
    comp_stocks_sub = ComponentStock.objects.filter(component=OuterRef('pk'))
    if rule.tenant:
        comp_stocks_sub = comp_stocks_sub.filter(location__tenant=rule.tenant)
    comp_allocs_sub = ComponentAllocation.objects.filter(
        component=OuterRef('pk'), deleted_at__isnull=True,
    )
    if rule.tenant:
        comp_allocs_sub = comp_allocs_sub.filter(assigned_asset__tenant=rule.tenant)
    comp_qs = Component.objects.filter(deleted_at__isnull=True).annotate(
        annotated_total_stock=Coalesce(
            Subquery(comp_stocks_sub.values('component').annotate(t=Sum('qty')).values('t')), 0
        ),
        annotated_allocated_stock=Coalesce(
            Subquery(comp_allocs_sub.values('component').annotate(t=Sum('qty')).values('t')), 0
        ),
    )
    if rule.tenant:
        comp_qs = comp_qs.filter(tenant=rule.tenant)

    for comp in comp_qs:
        available = max(0, comp.annotated_total_stock - comp.annotated_allocated_stock)
        threshold = comp.min_qty if (comp.min_qty and comp.min_qty > 0) else rule.threshold_value
        if available <= threshold:
            matches.append({
                'obj': comp, 'tenant': rule.tenant,
                'subject': f"Low Stock: {comp.name}",
                'message': (
                    f"Component '{comp.name}' available stock is {available}, "
                    f"at or below the safety limit of {threshold} units."
                ),
            })

    return matches


def _match_upcoming_eol(rule, today):
    from assets.models import Asset

    deadline = today + timezone.timedelta(days=rule.threshold_value)
    assets = Asset.objects.filter(
        deleted_at__isnull=True,
        purchase_date__isnull=False,
        asset_type__eol_months__gt=0,
    ).select_related('asset_type')
    if rule.tenant:
        assets = assets.filter(tenant=rule.tenant)

    matches = []
    for asset in assets:
        eol = asset.eol_date
        if eol and today <= eol <= deadline:
            days_left = (eol - today).days
            matches.append({
                'obj': asset, 'tenant': asset.tenant,
                'subject': f"Upcoming Hardware EOL: {asset.asset_tag}",
                'message': (
                    f"Asset {asset.asset_tag} ({asset.name}) reaches EOL on "
                    f"{eol:%Y-%m-%d} ({days_left} day(s) remaining)."
                ),
            })
    return matches


def _match_license_expiry(rule, today):
    from licenses.models import License

    deadline = today + timezone.timedelta(days=rule.threshold_value)
    qs = License.objects.filter(
        deleted_at__isnull=True,
        expiration_date__lte=deadline,
        expiration_date__gte=today,
    )
    if rule.tenant:
        qs = qs.filter(tenant=rule.tenant)

    matches = []
    for lic in qs:
        days_left = (lic.expiration_date - today).days
        matches.append({
            'obj': lic, 'tenant': lic.tenant,
            'subject': f"License Expiring: {lic.name}",
            'message': (
                f"License '{lic.name}' expires on {lic.expiration_date} "
                f"({days_left} day(s) remaining)."
            ),
        })
    return matches


def _match_renewal_due(rule, today):
    from subscriptions.models import Subscription

    deadline = today + timezone.timedelta(days=rule.threshold_value)
    qs = Subscription.objects.filter(
        deleted_at__isnull=True,
        status='active',
        renewal_date__lte=deadline,
        renewal_date__gte=today,
    )
    if rule.tenant:
        qs = qs.filter(tenant=rule.tenant)

    matches = []
    for sub in qs:
        days_left = (sub.renewal_date - today).days
        matches.append({
            'obj': sub, 'tenant': sub.tenant,
            'subject': f"Subscription Renewal Due: {sub.name}",
            'message': (
                f"Subscription '{sub.name}' ends on {sub.renewal_date} "
                f"({days_left} day(s) remaining) and requires renewal validation."
            ),
        })
    return matches


def _match_warranty_expiry(rule, today):
    from assets.models import Asset

    deadline = today + timezone.timedelta(days=rule.threshold_value)
    qs = Asset.objects.filter(
        deleted_at__isnull=True,
        warranty_expiration__lte=deadline,
        warranty_expiration__gte=today,
    ).select_related('asset_type')
    if rule.tenant:
        qs = qs.filter(tenant=rule.tenant)

    matches = []
    for asset in qs:
        days_left = (asset.warranty_expiration - today).days
        matches.append({
            'obj': asset, 'tenant': asset.tenant,
            'subject': f"Warranty Expiring: {asset.asset_tag}",
            'message': (
                f"Asset {asset.asset_tag} ({asset.name}) warranty expires on "
                f"{asset.warranty_expiration:%Y-%m-%d} ({days_left} day(s) remaining)."
            ),
        })
    return matches


def _match_audit_overdue(rule, today):
    """Assets whose last audit is older than threshold_value days, or never audited.

    threshold_value = number of days since the last audit before it counts as overdue.
    An asset that has never been audited (last_audited IS NULL) is always included.
    """
    from assets.models import Asset
    from django.db.models import Q

    cutoff = today - timezone.timedelta(days=rule.threshold_value)
    qs = Asset.objects.filter(
        deleted_at__isnull=True,
    ).filter(
        Q(last_audited__isnull=True) | Q(last_audited__lt=cutoff)
    )
    if rule.tenant:
        qs = qs.filter(tenant=rule.tenant)

    matches = []
    for asset in qs:
        if asset.last_audited:
            days_overdue = (today - asset.last_audited).days
            detail = f"last audited {days_overdue} day(s) ago ({asset.last_audited:%Y-%m-%d})"
        else:
            detail = "never audited"
        matches.append({
            'obj': asset, 'tenant': asset.tenant,
            'subject': f"Audit Overdue: {asset.asset_tag}",
            'message': (
                f"Asset {asset.asset_tag} ({asset.name}) is overdue for an audit "
                f"({detail}; threshold: every {rule.threshold_value} day(s))."
            ),
        })
    return matches
