import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.context import _current_user, set_current_membership, set_current_tenant
from core.events import DeliveryDisposition, delivery_log_context, delivery_log_message, send_notification_to_channel
from core.tasks.context import TaskContext
from extras.models import AlertLog, AlertRule

logger = logging.getLogger(__name__)


def evaluate_alert_rules_task() -> int:
    """
    Scheduled daily task: evaluate all active AlertRules, create AlertLogs for
    new matches, auto-resolve logs whose conditions have cleared, re-notify
    unresolved alerts on the configured cadence, and dispatch channel
    notifications (unless the rule is muted).
    """
    # Run as a true system context: clear any ambient tenant/membership AND the
    # current user. A non-superuser principal left bound here makes the tenant-
    # scoping managers fail closed (both the rule query and the open-log
    # prefetch return nothing), silently breaking evaluation and dedup.
    set_current_tenant(None)
    set_current_membership(None)
    _current_user.set(None)

    active_rules = AlertRule.objects.filter(is_active=True).select_related("tenant")
    logger.info("Evaluating %d active alert rules...", active_rules.count())

    today = timezone.now().date()
    existing_logs = _prefetch_open_logs()
    alerts_triggered_count = 0

    for rule in active_rules:
        logger.info(
            "Evaluating rule: %s (type=%s, threshold=%s, muted=%s, renotify=%s)",
            rule.name,
            rule.alert_type,
            rule.threshold_value,
            rule.is_muted,
            rule.renotify_interval_days,
        )
        # Use a system-level TaskContext; no specific user_id for scheduled tasks.
        with TaskContext(tenant_id=rule.tenant_id):
            alerts_triggered_count += _evaluate_rule(rule, today, existing_logs)

    logger.info(
        "Alert evaluation complete. Triggered %d fresh alert(s).",
        alerts_triggered_count,
    )
    return alerts_triggered_count


def run_alert_rule_now(rule_id: int) -> int:
    """Evaluate a single AlertRule immediately (used by the 'Run now' UI action).

    Runs as a system context: the tenant, membership and current-user
    contextvars are cleared (and NOT restored) so rule selection and open-log
    dedup are not constrained by an ambient (possibly non-superuser) principal.
    Because the contextvars are not restored, callers must run this standalone
    in a worker (the 'Run now' view enqueues it via async_task) rather than
    inline inside a request.

    Returns the number of fresh alerts triggered.
    """
    set_current_tenant(None)
    set_current_membership(None)
    _current_user.set(None)

    rule = AlertRule.objects.filter(pk=rule_id, is_active=True).select_related("tenant").first()
    if not rule:
        logger.warning("run_alert_rule_now: rule %s not found or inactive.", rule_id)
        return 0

    today = timezone.now().date()
    existing_logs = _prefetch_open_logs(rule_id=rule.pk)

    with TaskContext(tenant_id=rule.tenant_id):
        return _evaluate_rule(rule, today, existing_logs)


def _prefetch_open_logs(rule_id=None):
    """Map of (rule_id, content_type_id, object_id) -> AlertLog for open alerts.

    Uses ``unscoped`` (the cross-tenant manager) so the dedup map always spans
    every tenant's open logs regardless of the ambient tenant/user context. The
    tenant-scoping default manager would fail closed to an empty queryset when a
    non-superuser principal is bound with no active tenant, causing a fresh log
    to be created on every evaluation (the duplicate-AlertLog bug).
    """
    qs = AlertLog.unscoped.filter(status__in=[AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED])
    if rule_id is not None:
        qs = qs.filter(rule_id=rule_id)
    return {(log.rule_id, log.content_type_id, log.object_id): log for log in qs}


def _schedule_alert_dispatch(rule, match, alert_log):
    """Dispatch one persisted alert after commit and record disposition state."""
    alert_id = alert_log.pk
    rule_id = rule.pk
    tenant_id = alert_log.tenant_id
    alert_match = dict(match)

    def dispatch_alert():
        notified_at = timezone.now()
        try:
            with TaskContext(tenant_id=tenant_id):
                rule_for_dispatch = AlertRule._base_manager.get(pk=rule_id)
                persisted_alert = AlertLog.unscoped.get(pk=alert_id)
                delivery = _dispatch_channels(rule_for_dispatch, alert_match, persisted_alert)
        except Exception:
            logger.exception("Alert delivery failed for AlertLog %s.", alert_id)
            delivery = {"__dispatch__": DeliveryDisposition.TERMINAL.value}
        try:
            AlertLog.unscoped.filter(pk=alert_id).update(
                delivery_status=delivery,
                last_notified_at=notified_at,
            )
        except Exception:
            # A metadata-write failure must not escape the on_commit callback:
            # Django stops executing later callbacks when one raises.
            logger.exception("Alert delivery metadata update failed for AlertLog %s.", alert_id)

    transaction.on_commit(dispatch_alert)


def _evaluate_rule(rule, today, existing_logs):
    """Evaluate one rule: create/renotify alerts and auto-resolve cleared ones.

    Returns the count of freshly-created alerts. Mutates ``existing_logs`` in place.
    """
    from django.contrib.contenttypes.models import ContentType

    now = timezone.now()
    fresh_count = 0
    matched_keys = set()  # (content_type_id, object_id) for this rule run
    scheduled_dispatch_keys = set()  # at most one callback per alert in this pass

    try:
        matches = _collect_matches(rule, today)
    except Exception:
        logger.exception("Error collecting matches for rule %s", rule.name)
        matches = []

    for match in matches:
        obj = match["obj"]
        ct = ContentType.objects.get_for_model(obj)
        key = (rule.id, ct.id, obj.pk)
        matched_keys.add((ct.id, obj.pk))

        existing = existing_logs.get(key)

        if existing is None:
            # New alert. Guard the create with a savepoint: the partial unique
            # constraint (one open alert per rule+object) can fire if the
            # prefetch missed an open row (a concurrent evaluation, or a context
            # that scoped the prefetch out). On conflict, adopt the existing open
            # row instead of crashing the task and poisoning the transaction.
            try:
                with transaction.atomic():
                    alert_log = AlertLog.objects.create(
                        rule=rule,
                        subject=match["subject"],
                        message=match["message"],
                        severity=rule.severity,
                        content_type=ct,
                        object_id=obj.pk,
                        tenant=match.get("tenant"),
                        delivery_status=({"__dispatch__": "pending"} if not rule.is_muted else {}),
                    )
            except IntegrityError:
                # The partial unique constraint fired (or, rarely, another
                # integrity error). Adopt the existing open row so the task does
                # not crash; log so a non-constraint conflict is never silent.
                logger.warning(
                    "AlertLog create conflicted for rule '%s' on '%s'; adopting existing open row.",
                    rule.name,
                    obj,
                )
                alert_log = (
                    AlertLog.unscoped.filter(
                        rule=rule,
                        content_type=ct,
                        object_id=obj.pk,
                        status__in=[AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED],
                    )
                    .order_by("created_at")
                    .first()
                )
                if alert_log is None:
                    # No open row to adopt: the conflict was not the expected
                    # open-alert constraint (or the row vanished). Skip this
                    # match rather than mask the cause silently.
                    logger.error(
                        "AlertLog create failed for rule '%s' on '%s' with no open row to adopt; skipping.",
                        rule.name,
                        obj,
                    )
                    continue
                existing_logs[key] = alert_log
                # Treat the adopted row as the existing alert (re-notify below).
                existing = alert_log
            else:
                existing_logs[key] = alert_log
                fresh_count += 1
                logger.info("Triggered AlertLog %s for '%s' on '%s'.", alert_log.pk, match["subject"], obj)
                if not rule.is_muted:
                    # Mark the in-memory row as scheduled so duplicate matches in
                    # one evaluation cannot immediately re-notify it before commit.
                    alert_log.last_notified_at = now

                    _schedule_alert_dispatch(rule, match, alert_log)
                    scheduled_dispatch_keys.add(key)

        if existing is not None and not rule.is_muted:
            # Retry a committed-but-undelivered alert, or re-notify on cadence.
            pending = (existing.delivery_status or {}).get("__dispatch__") == "pending"
            ref = existing.last_notified_at or existing.created_at
            due = ref and (now - ref) >= timezone.timedelta(days=rule.renotify_interval_days)
            if (pending or (rule.renotify_interval_days > 0 and due)) and key not in scheduled_dispatch_keys:
                AlertLog.unscoped.filter(pk=existing.pk).update(
                    delivery_status={"__dispatch__": "pending"},
                )
                existing.delivery_status = {"__dispatch__": "pending"}
                existing.last_notified_at = now
                _schedule_alert_dispatch(rule, match, existing)
                scheduled_dispatch_keys.add(key)
                logger.info("Re-notified AlertLog %s for '%s'.", existing.pk, existing.subject)

    # Auto-resolve logs whose conditions have cleared.
    _auto_resolve_cleared(rule, matched_keys)

    # Record evaluation time without tripping the change log (system bookkeeping).
    AlertRule.objects.filter(pk=rule.pk).update(last_fired_at=now)

    return fresh_count


def _dispatch_channels(rule, match, alert_log):
    """Send notifications to all explicitly attached channels; return per-channel delivery dict."""
    channels = rule.channels.all()
    if not channels.exists():
        return {"__no_channels__": "no channels attached to this rule"}

    delivery = {}
    for channel in channels:
        try:
            result = send_notification_to_channel(channel, match["subject"], match["message"])
            delivery[str(channel.pk)] = "ok" if result else result.disposition.value
            if not result:
                context = delivery_log_context(result.operation, tenant_id=rule.tenant_id)
                logger.warning(
                    "%s disposition=%s user_visible=%s",
                    delivery_log_message(context),
                    result.disposition.value,
                    result.user_visible,
                )
        # broad except: boundary-isolation: third-party notification backends expose non-enumerable exceptions
        except Exception:
            delivery[str(channel.pk)] = DeliveryDisposition.TERMINAL.value
            context = delivery_log_context("alert.channel.dispatch", tenant_id=rule.tenant_id)
            logger.error(
                "%s disposition=terminal reason=unexpected_channel_error channel_id=%s",
                delivery_log_message(context),
                channel.pk,
            )
    return delivery


def _auto_resolve_cleared(rule, matched_keys):
    """
    Flip active/acknowledged logs to resolved when their condition no longer
    matches, so the rule can re-fire if the object dips below threshold again.
    """
    stale_qs = AlertLog.unscoped.filter(
        rule=rule,
        status__in=[AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED],
    )
    resolved_count = 0
    for log in stale_qs:
        if (log.content_type_id, log.object_id) not in matched_keys:
            log.status = AlertLog.STATUS_RESOLVED
            log.resolved_at = timezone.now()
            log.save(update_fields=["status", "resolved_at"])
            resolved_count += 1

    if resolved_count:
        logger.info("Auto-resolved %d cleared alert(s) for rule '%s'.", resolved_count, rule.name)


def _collect_matches(rule, today):
    """Return list of match dicts for the given rule and date."""
    from django.db.models import OuterRef, Q, Subquery, Sum
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
    from django.db.models import OuterRef, Subquery, Sum
    from django.db.models.functions import Coalesce

    from inventory.models import (
        Accessory,
        AccessoryAssignment,
        AccessoryStock,
        Component,
        ComponentAllocation,
        ComponentStock,
        Consumable,
        ConsumableAssignment,
        ConsumableStock,
    )

    matches = []

    # --- Accessories ---
    acc_stocks_sub = AccessoryStock.objects.filter(accessory=OuterRef("pk"))
    if rule.tenant:
        acc_stocks_sub = acc_stocks_sub.filter(location__tenant=rule.tenant)
    acc_assigns_sub = AccessoryAssignment.objects.filter(
        accessory=OuterRef("pk"),
        from_location__isnull=True,
    )
    acc_qs = Accessory.objects.filter(deleted_at__isnull=True).annotate(
        annotated_total_stock=Coalesce(
            Subquery(acc_stocks_sub.values("accessory").annotate(t=Sum("qty")).values("t")), 0
        ),
        annotated_undeducted_qty=Coalesce(
            Subquery(acc_assigns_sub.values("accessory").annotate(t=Sum("qty")).values("t")), 0
        ),
    )
    if rule.tenant:
        acc_qs = acc_qs.filter(tenant=rule.tenant)

    for acc in acc_qs:
        available = max(0, acc.annotated_total_stock - acc.annotated_undeducted_qty)
        threshold = acc.min_qty if (acc.min_qty and acc.min_qty > 0) else rule.threshold_value
        if available <= threshold:
            matches.append(
                {
                    "obj": acc,
                    "tenant": acc.tenant,
                    "subject": _("Low Stock: %(name)s") % {"name": acc.name},
                    "message": _(
                        "Accessory '%(name)s' available stock is %(available)s, "
                        "at or below the safety limit of %(threshold)s units."
                    )
                    % {"name": acc.name, "available": available, "threshold": threshold},
                }
            )

    # --- Consumables ---
    con_stocks_sub = ConsumableStock.objects.filter(consumable=OuterRef("pk"))
    if rule.tenant:
        con_stocks_sub = con_stocks_sub.filter(location__tenant=rule.tenant)
    con_assigns_sub = ConsumableAssignment.objects.filter(
        consumable=OuterRef("pk"),
        from_location__isnull=True,
    )
    con_qs = Consumable.objects.filter(deleted_at__isnull=True).annotate(
        annotated_total_stock=Coalesce(
            Subquery(con_stocks_sub.values("consumable").annotate(t=Sum("qty")).values("t")), 0
        ),
        annotated_undeducted_qty=Coalesce(
            Subquery(con_assigns_sub.values("consumable").annotate(t=Sum("qty")).values("t")), 0
        ),
    )
    if rule.tenant:
        con_qs = con_qs.filter(tenant=rule.tenant)

    for con in con_qs:
        available = max(0, con.annotated_total_stock - con.annotated_undeducted_qty)
        threshold = con.min_qty if (con.min_qty and con.min_qty > 0) else rule.threshold_value
        if available <= threshold:
            matches.append(
                {
                    "obj": con,
                    "tenant": con.tenant,
                    "subject": _("Low Stock: %(name)s") % {"name": con.name},
                    "message": _(
                        "Consumable '%(name)s' available stock is %(available)s, "
                        "at or below the safety limit of %(threshold)s units."
                    )
                    % {"name": con.name, "available": available, "threshold": threshold},
                }
            )

    # --- Components ---
    comp_stocks_sub = ComponentStock.objects.filter(component=OuterRef("pk"))
    if rule.tenant:
        comp_stocks_sub = comp_stocks_sub.filter(location__tenant=rule.tenant)
    comp_allocs_sub = ComponentAllocation.objects.filter(
        component=OuterRef("pk"),
        deleted_at__isnull=True,
    )
    if rule.tenant:
        comp_allocs_sub = comp_allocs_sub.filter(assigned_asset__tenant=rule.tenant)
    comp_qs = Component.objects.filter(deleted_at__isnull=True).annotate(
        annotated_total_stock=Coalesce(
            Subquery(comp_stocks_sub.values("component").annotate(t=Sum("qty")).values("t")), 0
        ),
        annotated_allocated_stock=Coalesce(
            Subquery(comp_allocs_sub.values("component").annotate(t=Sum("qty")).values("t")), 0
        ),
    )
    if rule.tenant:
        comp_qs = comp_qs.filter(tenant=rule.tenant)

    for comp in comp_qs:
        available = max(0, comp.annotated_total_stock - comp.annotated_allocated_stock)
        threshold = comp.min_qty if (comp.min_qty and comp.min_qty > 0) else rule.threshold_value
        if available <= threshold:
            matches.append(
                {
                    "obj": comp,
                    "tenant": comp.tenant,
                    "subject": _("Low Stock: %(name)s") % {"name": comp.name},
                    "message": _(
                        "Component '%(name)s' available stock is %(available)s, "
                        "at or below the safety limit of %(threshold)s units."
                    )
                    % {"name": comp.name, "available": available, "threshold": threshold},
                }
            )

    return matches


def _match_upcoming_eol(rule, today):
    from assets.models import Asset

    deadline = today + timezone.timedelta(days=rule.threshold_value)
    assets = Asset.objects.filter(
        deleted_at__isnull=True,
        purchase_date__isnull=False,
        asset_type__eol_months__gt=0,
    ).select_related("asset_type")
    if rule.tenant:
        assets = assets.filter(tenant=rule.tenant)

    matches = []
    for asset in assets:
        eol = asset.eol_date
        if eol and today <= eol <= deadline:
            days_left = (eol - today).days
            matches.append(
                {
                    "obj": asset,
                    "tenant": asset.tenant,
                    "subject": _("Upcoming Hardware EOL: %(tag)s") % {"tag": asset.asset_tag},
                    "message": _("Asset %(tag)s (%(name)s) reaches EOL on %(eol)s (%(days)s day(s) remaining).")
                    % {
                        "tag": asset.asset_tag,
                        "name": asset.name,
                        "eol": f"{eol:%Y-%m-%d}",
                        "days": days_left,
                    },
                }
            )
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
        matches.append(
            {
                "obj": lic,
                "tenant": lic.tenant,
                "subject": _("License Expiring: %(name)s") % {"name": lic.name},
                "message": _("License '%(name)s' expires on %(date)s (%(days)s day(s) remaining).")
                % {"name": lic.name, "date": lic.expiration_date, "days": days_left},
            }
        )
    return matches


def _match_renewal_due(rule, today):
    from subscriptions.models import Subscription

    deadline = today + timezone.timedelta(days=rule.threshold_value)
    qs = Subscription.objects.filter(
        deleted_at__isnull=True,
        status="active",
        renewal_date__lte=deadline,
        renewal_date__gte=today,
    )
    if rule.tenant:
        qs = qs.filter(tenant=rule.tenant)

    matches = []
    for sub in qs:
        days_left = (sub.renewal_date - today).days
        matches.append(
            {
                "obj": sub,
                "tenant": sub.tenant,
                "subject": _("Subscription Renewal Due: %(name)s") % {"name": sub.name},
                "message": _(
                    "Subscription '%(name)s' ends on %(date)s "
                    "(%(days)s day(s) remaining) and requires renewal validation."
                )
                % {"name": sub.name, "date": sub.renewal_date, "days": days_left},
            }
        )
    return matches


def _match_warranty_expiry(rule, today):
    from assets.models import Warranty

    deadline = today + timezone.timedelta(days=rule.threshold_value)
    qs = Warranty.objects.filter(
        deleted_at__isnull=True,
        asset__deleted_at__isnull=True,
        end_date__lte=deadline,
        end_date__gte=today,
    ).select_related("asset", "asset__asset_type")
    if rule.tenant:
        qs = qs.filter(asset__tenant=rule.tenant)

    matches = []
    for warranty in qs:
        asset = warranty.asset
        days_left = (warranty.end_date - today).days
        matches.append(
            {
                "obj": asset,
                "tenant": asset.tenant,
                "subject": _("Warranty Expiring: %(tag)s") % {"tag": asset.asset_tag},
                "message": _(
                    "Asset %(tag)s (%(name)s) %(wtype)s warranty expires on %(date)s (%(days)s day(s) remaining)."
                )
                % {
                    "tag": asset.asset_tag,
                    "name": asset.name,
                    "wtype": warranty.get_warranty_type_display(),
                    "date": f"{warranty.end_date:%Y-%m-%d}",
                    "days": days_left,
                },
            }
        )
    return matches


def _match_audit_overdue(rule, today):
    """Assets overdue for a physical audit.

    Per-category cadence (Category.audit_interval_months) takes priority when
    set on an asset's category; otherwise falls back to rule.threshold_value days.
    Assets that have never been audited and have a cadence are always included.
    """
    from django.db.models import Q

    from assets.models import Asset, Category

    cutoff = today - timezone.timedelta(days=rule.threshold_value)

    # Also pull in assets overdue per a category-level cadence that's shorter
    # than the global rule threshold (they wouldn't match last_audited__lt=cutoff).
    category_overdue_q = Q(pk__in=[])
    for cat in Category.objects.filter(audit_interval_months__isnull=False):
        cat_cutoff = today - timezone.timedelta(days=cat.audit_interval_months * 30)
        category_overdue_q |= Q(asset_type__category=cat) & (
            Q(last_audited__isnull=True) | Q(last_audited__lt=cat_cutoff)
        )

    qs = (
        Asset.objects.filter(
            deleted_at__isnull=True,
        )
        .select_related(
            "asset_type__category",
        )
        .filter(Q(last_audited__isnull=True) | Q(last_audited__lt=cutoff) | category_overdue_q)
    )
    if rule.tenant:
        qs = qs.filter(tenant=rule.tenant)

    matches = []
    for asset in qs:
        # Prefer category-level cadence over the global rule threshold.
        category = asset.asset_type.category if asset.asset_type else None
        if category and category.audit_interval_months:
            interval_days = category.audit_interval_months * 30
            base = asset.last_audited or asset.created_at
            due_date = (base + timezone.timedelta(days=interval_days)).date()
            if due_date > today:
                continue  # category cadence says not yet overdue
            threshold_desc = _("every %(months)s month(s) per category") % {
                "months": category.audit_interval_months,
            }
        else:
            threshold_desc = _("every %(days)s day(s)") % {"days": rule.threshold_value}

        if asset.last_audited:
            days_overdue = (today - asset.last_audited.date()).days
            detail = _("last audited %(days)s day(s) ago (%(date)s)") % {
                "days": days_overdue,
                "date": f"{asset.last_audited:%Y-%m-%d}",
            }
        else:
            detail = _("never audited")
        matches.append(
            {
                "obj": asset,
                "tenant": asset.tenant,
                "subject": _("Audit Overdue: %(tag)s") % {"tag": asset.asset_tag},
                "message": _("Asset %(tag)s (%(name)s) is overdue for an audit (%(detail)s; threshold: %(threshold)s).")
                % {
                    "tag": asset.asset_tag,
                    "name": asset.name,
                    "detail": detail,
                    "threshold": threshold_desc,
                },
            }
        )
    return matches
