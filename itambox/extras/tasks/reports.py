import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from core.context import get_current_user
from core.csv_utils import safe_csv_filename
from core.events import send_notification_to_channel
from core.models import EmailSettings
from core.reports import build_report_context
from core.reports.rendering import render_report_csv, render_report_html
from core.tasks.context import TaskContext
from core.tasks.utils import TaskResult, TaskStatus, classify_task_error
from extras.models import (
    FileAttachment,
    NotificationChannel,
    ReportGenerationArchive,
    ScheduledReport,
    ScheduledReportFire,
    ScheduledReportScopeAuthorization,
)

logger = logging.getLogger(__name__)

#: Delivery ledger target identities: the email fan-out is one aggregate
#: message to all recipients; each notification channel is its own target, so
#: a failed channel can be re-attempted by ``Retry delivery`` without touching
#: the targets that already received the report.
DELIVERY_TARGET_EMAIL = "email"

#: How long a ``Retry delivery`` claim stays exclusive before another request
#: may take it over. Mirrors the alert-dispatch claim lease: a claim left
#: behind by an interrupted request is recoverable after the lease expires,
#: and two parallel requests can never both contact the same failed targets.
RETRY_CLAIM_LEASE = timedelta(minutes=15)


def _channel_target_key(channel):
    return f"channel:{channel.pk}"


def delivery_ledger_message(targets):
    """Render a human-readable, non-truncated delivery ledger for task results."""
    return "; ".join(
        f"{target.get('label') or target.get('target')}: {target.get('status')}"
        + (f" ({target.get('error')})" if target.get("error") else "")
        for target in targets
    )


def _parse_intended_fire_at(value):
    """Parse the intended occurrence timestamp injected by the django-q scheduler.

    The scheduler passes ``Schedule.next_run.isoformat()`` (timezone-aware in
    the project timezone). Unparseable values are ignored (the run proceeds
    without a claim, exactly like a manual invocation) instead of failing the
    task: the claim is a safety net, not a delivery requirement.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            logger.warning("Ignoring unparseable intended fire timestamp: %r", value)
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _claim_fire(sched, fire_at):
    """Atomically claim one intended occurrence by its exact fire time.

    Idempotency is per ``(schedule, occurrence)``: the unique fire record
    makes the insert the fence, so a broker redelivery of the same occurrence
    is an exact-match no-op, and out-of-order execution (parallel workers, a
    catch-up backlog after downtime) can never let a newer occurrence discard
    an older, not-yet-accepted one. ``last_accepted_fire_at`` remains a
    monotonic informational marker and is never a rejection criterion.
    """
    try:
        with transaction.atomic():
            ScheduledReportFire.objects.create(schedule_id=sched.pk, intended_fire_at=fire_at)
    except IntegrityError:
        return False
    ScheduledReport._base_manager.filter(pk=sched.pk).filter(
        Q(last_accepted_fire_at__isnull=True) | Q(last_accepted_fire_at__lt=fire_at)
    ).update(last_accepted_fire_at=fire_at)
    if sched.last_accepted_fire_at is None or sched.last_accepted_fire_at < fire_at:
        sched.last_accepted_fire_at = fire_at
    return True


def _render_report_html(context_data, template=None):
    """Compatibility hook for existing task tests and integrations."""
    return render_report_html(context_data, template)


@dataclass
class _ReportOutput:
    email_body: str
    attachment_content: bytes | str | None = None
    attachment_filename: str = ""
    attachment_mime: str = ""


@dataclass
class _DeliveryOutcome:
    attempted: int = 0
    succeeded: int = 0
    failures: list[str] = field(default_factory=list)
    #: Per-target ledger: {"target", "label", "status", "error", "retried"}.
    targets: list[dict] = field(default_factory=list)

    @property
    def status(self):
        if not self.attempted:
            return "none"
        if not self.failures:
            return "success"
        return "partial" if self.succeeded else "failed"

    def record_success(self, *, target="", label="", details=None):
        self.attempted += 1
        self.succeeded += 1
        if target:
            self.targets.append(
                {
                    "target": target,
                    "label": label or target,
                    "status": "ok",
                    "error": "",
                    "retried": False,
                    "details": details or {},
                }
            )

    def record_failure(self, failure, *, target="", label="", details=None):
        self.attempted += 1
        self.failures.append(str(failure))
        if target:
            self.targets.append(
                {
                    "target": target,
                    "label": label or target,
                    "status": "failed",
                    "error": str(failure),
                    "retried": False,
                    "details": details or {},
                }
            )

    def merge(self, other):
        self.attempted += other.attempted
        self.succeeded += other.succeeded
        self.failures.extend(other.failures)
        self.targets.extend(other.targets)


def _report_filename(template, extension):
    return f"{safe_csv_filename(template.name).lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.{extension}"


def _attachment_email_body(format_name, report_name, timestamp=None, disclosure_text=""):
    body = _("Attached is the scheduled %(format)s report for '%(name)s', generated on %(timestamp)s UTC.") % {
        "format": format_name,
        "name": report_name,
        "timestamp": f"{(timestamp or timezone.now()):%Y-%m-%d %H:%M:%S}",
    }
    if disclosure_text:
        body = f"{body}\n\n{disclosure_text}"
    return body


def _render_report_output(sched, template, headers, rows, context_data):
    disclosure_text = context_data.get("disclosure_text", "")
    if sched.format == ScheduledReport.FORMAT_HTML:
        return _ReportOutput(email_body=_render_report_html(context_data, template))

    if sched.format == ScheduledReport.FORMAT_PDF:
        # inline import: heavy-import: PDF exporter is needed only for PDF schedules
        from core.reports.exporters import PDF_MIME, report_pdf_bytes

        return _ReportOutput(
            email_body=_attachment_email_body("PDF", template.name, disclosure_text=disclosure_text),
            attachment_content=report_pdf_bytes(_render_report_html(context_data, template)),
            attachment_filename=_report_filename(template, "pdf"),
            attachment_mime=PDF_MIME,
        )

    if sched.format == ScheduledReport.FORMAT_XLSX:
        # inline import: heavy-import: spreadsheet exporter is needed only for XLSX schedules
        from core.reports.exporters import XLSX_MIME, report_xlsx_bytes

        return _ReportOutput(
            email_body=_attachment_email_body("XLSX", template.name, disclosure_text=disclosure_text),
            attachment_content=report_xlsx_bytes(
                headers, rows, sheet_title=template.name, disclosure_text=disclosure_text
            ),
            attachment_filename=_report_filename(template, "xlsx"),
            attachment_mime=XLSX_MIME,
        )

    if sched.format == ScheduledReport.FORMAT_CSV:
        csv_content = render_report_csv(
            template,
            headers,
            rows,
            summary_cards=context_data.get("summary_cards"),
            grouped_data=context_data.get("grouped_data"),
            disclosure_text=disclosure_text,
        )
        return _ReportOutput(
            email_body=_attachment_email_body("CSV", template.name, disclosure_text=disclosure_text),
            attachment_content=csv_content,
            attachment_filename=_report_filename(template, "csv"),
            attachment_mime="text/csv",
        )

    raise ValueError(f"Unsupported scheduled report format: {sched.format}")


def _archive_report_output(sched, template, output, active_tenant, disclosure_text="", generation_scope=None):
    if not getattr(sched, "save_to_archive", True):
        return None

    archive_entry = ReportGenerationArchive.objects.create(
        scheduled_report=sched,
        format=sched.format,
        status="running",
        tenant=active_tenant,
        disclosure_text=disclosure_text or "",
        generation_scope=generation_scope or {},
    )
    if sched.format == ScheduledReport.FORMAT_HTML:
        content_bytes = output.email_body.encode("utf-8")
        mime = "text/html"
        filename = _report_filename(template, "html")
    else:
        if output.attachment_content is None:
            raise ValueError("Scheduled report produced no attachment content")
        content_bytes = (
            output.attachment_content.encode("utf-8")
            if isinstance(output.attachment_content, str)
            else output.attachment_content
        )
        mime = output.attachment_mime or "application/octet-stream"
        filename = output.attachment_filename

    content_file = ContentFile(content_bytes, name=filename)
    file_attach = FileAttachment.objects.create(
        content_object=archive_entry,
        file=content_file,
        name=filename,
        mime_type=mime,
    )
    archive_entry.file = file_attach
    archive_entry.status = "success"
    archive_entry.save()
    return archive_entry


def _resolve_report_recipients(sched):
    return [recipient.strip() for recipient in sched.recipients.split(",") if recipient.strip()]


def _deliver_report_email(sched, template, output, recipient_list=None):
    recipient_list = _resolve_report_recipients(sched) if recipient_list is None else recipient_list
    if not recipient_list:
        return False

    email_config = EmailSettings.load()
    if not email_config or not email_config.enabled:
        raise ValidationError(_("SMTP Outbound Email is disabled in settings."))

    email = EmailMessage(
        subject=_("[Scheduled Report] %(name)s") % {"name": sched.name},
        body=output.email_body,
        from_email=email_config.from_address,
        to=recipient_list,
    )
    if sched.format == ScheduledReport.FORMAT_HTML:
        email.content_subtype = "html"
    elif output.attachment_content:
        email.attach(output.attachment_filename, output.attachment_content, output.attachment_mime)
    email.send(fail_silently=False)
    return True


def _deliver_report_channels(sched, summary_cards, total_rows, disclosure_text=""):
    report_subject = _("[Scheduled Report] %(name)s") % {"name": sched.name}
    card_lines = "\n".join("%s: %s" % (card.get("label"), card.get("value")) for card in (summary_cards or [])) or (
        _("Rows: %(n)s") % {"n": total_rows}
    )
    report_body = _(
        "Scheduled report '%(name)s' was generated on %(timestamp)s UTC.\nFormat: %(format)s\n%(summary)s"
    ) % {
        "name": sched.name,
        "timestamp": f"{timezone.now():%Y-%m-%d %H:%M:%S}",
        "format": sched.format.upper(),
        "summary": card_lines,
    }
    if disclosure_text:
        report_body = f"{report_body}\n{disclosure_text}"
    outcome = _DeliveryOutcome()
    for channel in sched.channels.all():
        if not channel.enabled:
            continue
        target_key = _channel_target_key(channel)
        # The delivered payload is recorded per target so Retry delivery can
        # re-send the original notification (subject and body with its summary
        # cards) instead of a reduced reconstruction.
        details = {
            "channel_id": getattr(channel, "pk", None),
            "payload": {"subject": report_subject, "body": report_body},
        }
        try:
            delivered = send_notification_to_channel(channel, report_subject, report_body)
        # broad except: boundary-isolation: channel integrations may raise implementation-specific failures
        except Exception as error:
            logger.error(
                "Scheduled report channel delivery failed",
                extra={
                    "operation": "reports.channel_delivery",
                    "channel_id": getattr(channel, "pk", None),
                    "exception_type": type(error).__name__,
                },
            )
            outcome.record_failure("channel.delivery_failed", target=target_key, label=channel.name, details=details)
        else:
            if delivered:
                outcome.record_success(target=target_key, label=channel.name, details=details)
            else:
                logger.warning(
                    "Scheduled report channel reported delivery failure",
                    extra={"operation": "reports.channel_delivery", "channel_id": getattr(channel, "pk", None)},
                )
                outcome.record_failure(
                    "channel.delivery_rejected", target=target_key, label=channel.name, details=details
                )
    return outcome


def _resolve_report_scope(sched):
    active_tenant = sched.tenant or (sched.report.tenant if sched.report else None)
    # Resolve the persisted scope through unscoped through-table reads: the
    # ambient tenant (bound in request threads and left behind by permission
    # checks) would otherwise silently truncate the M2M read. The explicit
    # filter list stays empty when no filter tenants are configured; the owner
    # tenant travels separately as active_tenant.
    if hasattr(sched, "persisted_scope_tenant_ids"):
        scope_ids = sched.persisted_scope_tenant_ids()
    else:
        filter_relation = list(sched.filter_tenants.all())
        if not filter_relation and sched.report:
            filter_relation = list(sched.report.filter_tenants.all())
        scope_ids = sorted({tenant.pk for tenant in filter_relation})
    filter_tenants = []
    if scope_ids:
        # inline import: heavy-import: organization models are only needed for tenant resolution
        from django.apps import apps

        Tenant = apps.get_model("organization", "Tenant")
        # Tenant.all_objects/objects apply ambient-tenant scoping; only the
        # plain base manager returns the full persisted scope. Keep the
        # soft-delete filter the scoped managers previously enforced.
        filter_tenants = list(Tenant._base_manager.filter(pk__in=scope_ids, deleted_at__isnull=True).order_by("pk"))
        if not filter_tenants:
            # Every explicitly scoped tenant is soft-deleted. Fail closed instead
            # of silently substituting the owner tenant's data.
            logger.error(
                "Scheduled report scope tenants are all soft-deleted; refusing compilation",
                extra={"operation": "reports.scope", "scheduled_report_id": getattr(sched, "pk", None)},
            )
            return None
    if active_tenant is None and not filter_tenants:
        logger.error(
            "Scheduled report has no tenant scope; refusing cross-tenant compilation",
            extra={"operation": "reports.scope", "scheduled_report_id": getattr(sched, "pk", None)},
        )
        return None
    return active_tenant, filter_tenants


def _scope_requires_authorization(active_tenant, filter_tenants):
    """Return whether persisted scope exceeds the schedule owner's tenant."""
    active_tenant_id = getattr(active_tenant, "pk", None)
    scope_tenant_ids = sorted({tenant.pk for tenant in filter_tenants})
    if not scope_tenant_ids and active_tenant_id is not None:
        scope_tenant_ids = [active_tenant_id]
    return active_tenant_id is None or scope_tenant_ids != [active_tenant_id]


def _load_scope_authorization(sched):
    """Load the durable approval for a scheduled report."""
    return (
        ScheduledReportScopeAuthorization.objects.filter(scheduled_report_id=sched.pk)
        .select_related("authorized_by")
        .first()
    )


def _stored_scope_tenants_are_live(authorization):
    """Whether every tenant of a stored approval still exists as a live row."""
    # inline import: heavy-import: organization models are only needed for tenant resolution
    from django.apps import apps

    Tenant = apps.get_model("organization", "Tenant")
    stored_ids = set(authorization.scope_tenant_ids)
    live_ids = set(Tenant._base_manager.filter(pk__in=stored_ids, deleted_at__isnull=True).values_list("pk", flat=True))
    return live_ids == stored_ids


def _approved_scope_tenants_removed(sched):
    """Whether an unrevoked approval lost its tenants, so the scope collapsed.

    Shared by generation and ``Retry delivery``: both must fail closed instead
    of silently substituting owner-tenant data for an approved broad scope.
    """
    authorization = ScheduledReportScopeAuthorization._base_manager.filter(scheduled_report=sched).first()
    return bool(
        authorization
        and authorization.scope_tenant_ids
        and authorization.revoked_at is None
        and not _stored_scope_tenants_are_live(authorization)
    )


def _parse_authorized_scope(authorization):
    try:
        return sorted({int(tenant_id) for tenant_id in authorization.scope_tenant_ids})
    except (TypeError, ValueError):
        return None


def _resolve_authorized_principal(authorization):
    try:
        principal = getattr(authorization, "authorized_by", None)
    except ObjectDoesNotExist:
        return None
    if principal is None or not getattr(principal, "is_active", False):
        return None
    return principal


def _tenant_scope_permission_is_valid(principal, tenant):
    tenant_id = getattr(tenant, "pk", None)
    if tenant_id is None:
        return False
    try:
        with TaskContext(
            tenant_id=tenant_id,
            user_id=principal.pk,
            operation="reports.scope_authorization",
        ):
            worker_principal = get_current_user()
            return worker_principal is not None and worker_principal.has_perm(
                "reports.view_cross_tenant_reports",
                obj=tenant,
            )
    except (ObjectDoesNotExist, PermissionDenied):
        return False


def _principal_can_authorize_scope(principal, filter_tenants):
    for tenant in filter_tenants:
        if not _tenant_scope_permission_is_valid(principal, tenant):
            return False
    return True


def _resolve_scope_authorization(sched, active_tenant, filter_tenants):
    """Resolve a current, durable principal approval for a broad schedule."""
    if not _scope_requires_authorization(active_tenant, filter_tenants):
        return None
    authorization = _load_scope_authorization(sched)
    # getattr keeps this compatible with the lightweight test doubles used in
    # the designer contract tests (SimpleNamespace rows have no revoked_at).
    if authorization is None or getattr(authorization, "revoked_at", None) is not None:
        return None
    scope_tenant_ids = sorted({tenant.pk for tenant in filter_tenants})
    authorized_scope = _parse_authorized_scope(authorization)
    if authorized_scope is None or authorized_scope != scope_tenant_ids:
        return None
    principal = _resolve_authorized_principal(authorization)
    if principal is None or not _principal_can_authorize_scope(principal, filter_tenants):
        return None
    return principal.pk


def _generation_scope_snapshot(sched, active_tenant, filter_tenants):
    """Record the tenant scope a generated archive is valid under.

    Retry delivery re-validates THIS archived scope instead of the schedule's
    current one: a later scope change or revocation must not legitimize
    redelivering an export that was compiled under a broader scope.
    """
    schedule_id = getattr(sched, "pk", None)
    authorization = _load_scope_authorization(sched) if schedule_id is not None else None
    filter_tenant_ids = sorted({tenant.pk for tenant in filter_tenants})
    active_tenant_id = getattr(active_tenant, "pk", None)
    data_tenant_ids = sorted(
        {tenant_id for tenant_id in [active_tenant_id, *filter_tenant_ids] if tenant_id is not None}
    )
    return {
        "active_tenant_id": active_tenant_id,
        "filter_tenant_ids": filter_tenant_ids,
        "data_tenant_ids": data_tenant_ids,
        "cross_tenant": _scope_requires_authorization(active_tenant, filter_tenants),
        "authorization_id": getattr(authorization, "pk", None),
    }


def _process_scheduled_report(sched, active_tenant, filter_tenants):
    archive_entry = None
    try:
        template = sched.report
        headers, rows, summary_cards, _grouped_data, _chart_svg, context_data = build_report_context(
            template,
            active_tenant=active_tenant,
            filter_tenants=filter_tenants,
        )
        context_data["scheduled_report"] = sched
        output = _render_report_output(sched, template, headers, rows, context_data)
        archive_entry = _archive_report_output(
            sched,
            template,
            output,
            active_tenant,
            context_data.get("disclosure_text", ""),
            generation_scope=_generation_scope_snapshot(sched, active_tenant, filter_tenants),
        )
    # broad except: task-isolation: one scheduled report failure must not abort the worker batch
    except Exception as error:
        status = classify_task_error(error)
        logger.error(
            "Scheduled report generation failed",
            extra={
                "operation": "reports.generate",
                "scheduled_report_id": getattr(sched, "pk", None),
                "exception_type": type(error).__name__,
            },
        )
        sched.last_status = f"{status.value}: report.generation_failed"
        sched.save()
        if archive_entry:
            archive_entry.status = "failed"
            archive_entry.error_message = "report.generation_failed"
            archive_entry.save()
        return TaskResult(status, "report.generation_failed", user_visible=True)

    delivery = _DeliveryOutcome()
    recipients = _resolve_report_recipients(sched)
    # Record the original recipients on the email target so Retry delivery
    # re-contacts exactly the recorded targets, not a since-edited list.
    email_details = {"recipients": recipients}
    if recipients:
        try:
            delivered = _deliver_report_email(sched, template, output, recipients)
        # broad except: boundary-isolation: SMTP providers expose implementation-specific delivery failures
        except Exception as error:
            logger.error(
                "Scheduled report email delivery failed",
                extra={
                    "operation": "reports.email_delivery",
                    "scheduled_report_id": getattr(sched, "pk", None),
                    "exception_type": type(error).__name__,
                },
            )
            delivery.record_failure("email.delivery_failed", target=DELIVERY_TARGET_EMAIL, details=email_details)
        else:
            if delivered:
                delivery.record_success(target=DELIVERY_TARGET_EMAIL, details=email_details)
            else:
                delivery.record_failure("email.delivery_rejected", target=DELIVERY_TARGET_EMAIL, details=email_details)

    delivery.merge(_deliver_report_channels(sched, summary_cards, len(rows), context_data.get("disclosure_text", "")))
    _persist_delivery_outcome(sched, archive_entry, delivery)
    if delivery.failures:
        logger.warning(
            "Scheduled report completed with delivery failures",
            extra={
                "operation": "reports.delivery",
                "scheduled_report_id": getattr(sched, "pk", None),
                "delivery_status": delivery.status,
            },
        )
        # Generation and archival completed.  Do not signal a task retry here:
        # retrying after a partial fan-out could duplicate already successful deliveries.
        status = TaskStatus.PARTIAL if delivery.succeeded else TaskStatus.TERMINAL
        return TaskResult(
            status,
            "report.delivery_partial" if delivery.succeeded else "report.delivery_failed",
            {"attempted": delivery.attempted, "succeeded": delivery.succeeded},
            message=delivery_ledger_message(delivery.targets),
            user_visible=True,
        )

    logger.info(
        "Scheduled report successfully processed",
        extra={"operation": "reports.generate", "scheduled_report_id": getattr(sched, "pk", None)},
    )
    return TaskResult(
        TaskStatus.SUCCESS,
        "report.completed",
        {"attempted": delivery.attempted, "succeeded": delivery.succeeded},
        user_visible=True,
    )


def _persist_delivery_outcome(sched, archive_entry, delivery):
    """Persist the delivery-stage outcome beside (never inside) generation fields.

    ``last_status`` carries the stable run outcome token only — detail is never
    truncated into it. The archive row keeps the per-target ledger, so compile,
    storage, and channel-delivery outcomes stay separable, and ``Retry
    delivery`` can re-attempt exactly the failed targets.
    """
    sched.last_status = "success" if not delivery.failures else delivery.status
    sched.save(update_fields=["last_status"])
    if archive_entry:
        archive_entry.delivery_status = delivery.status
        archive_entry.delivery_targets = delivery.targets
        archive_entry.save(update_fields=["delivery_status", "delivery_targets"])


def generate_scheduled_report_task(scheduled_report_id: int, intended_fire_at: str | None = None) -> TaskResult:
    """Compile and deliver one scheduled report inside a tenant-scoped task context.

    ``intended_fire_at`` is injected by the django-q scheduler (through the
    ``intended_date_kwarg`` registration) as the occurrence this firing
    represents. When present, the occurrence is claimed by its exact fire time
    before any work happens: a broker redelivery of the same occurrence is a
    no-op instead of a second dispatch, while a still-unaccepted older
    occurrence (out-of-order replay) is still processed exactly once. Manual
    invocations (the synchronous ``Run now`` path) pass no occurrence and
    execute unconditionally.
    """
    try:
        sched = ScheduledReport.objects.get(pk=scheduled_report_id)
    except ScheduledReport.DoesNotExist:
        logger.error(
            "Scheduled report not found",
            extra={"operation": "reports.generate", "scheduled_report_id": scheduled_report_id},
        )
        return TaskResult(TaskStatus.TERMINAL, "report.not_found")

    if not sched.is_active:
        logger.warning(
            "Scheduled report is inactive",
            extra={"operation": "reports.generate", "scheduled_report_id": sched.pk},
        )
        return TaskResult(TaskStatus.SKIPPED, "report.inactive")

    fire_at = _parse_intended_fire_at(intended_fire_at)
    if fire_at is not None and not _claim_fire(sched, fire_at):
        logger.info(
            "Scheduled report occurrence was already accepted; skipping redelivery",
            extra={
                "operation": "reports.generate",
                "scheduled_report_id": sched.pk,
                "intended_fire_at": fire_at.isoformat(),
            },
        )
        return TaskResult(TaskStatus.SKIPPED, "report.fire_already_accepted")

    scope = _resolve_report_scope(sched)
    if scope is None:
        return TaskResult(TaskStatus.TERMINAL, "report.scope_missing", user_visible=True)
    active_tenant, filter_tenants = scope
    if not filter_tenants and _approved_scope_tenants_removed(sched):
        # Soft-deleting a tenant strips its through rows, so an approved
        # broad scope silently collapses to the owner tenant. Fail closed
        # instead of substituting owner data. A deliberate wind-back (the
        # stored tenants are still live) runs single-tenant below.
        logger.error(
            "Scheduled report approved scope tenants were removed; refusing owner fallback",
            extra={"operation": "reports.scope", "scheduled_report_id": sched.pk},
        )
        return TaskResult(TaskStatus.TERMINAL, "report.scope_missing", user_visible=True)
    scope_authorized_user_id = _resolve_scope_authorization(sched, active_tenant, filter_tenants)
    scope_requires_authorization = _scope_requires_authorization(active_tenant, filter_tenants)
    if scope_requires_authorization and scope_authorized_user_id is None:
        logger.warning(
            "Scheduled report has no current durable authorization for its broad tenant scope",
            extra={"operation": "reports.scope", "scheduled_report_id": sched.pk},
        )
        return TaskResult(TaskStatus.TERMINAL, "report.scope_unauthorized", user_visible=True)

    with TaskContext(
        tenant_id=None if scope_requires_authorization else active_tenant.id if active_tenant else None,
        user_id=scope_authorized_user_id,
        operation="reports.generate",
        all_accessible=scope_requires_authorization,
    ) as ctx:
        logger.info(
            "Generating scheduled report",
            extra={**ctx.log_context, "scheduled_report_id": sched.pk},
        )
        sched.last_run = timezone.now()
        sched.save()
        return _process_scheduled_report(sched, active_tenant, filter_tenants)


@dataclass
class _RetryOutcome:
    """Result of a ``Retry delivery`` recovery attempt."""

    code: str
    retried: int = 0
    still_failed: int = 0
    detail: str = ""


def _retry_current_scope_is_authorized(sched):
    """Execution-time re-validation of the schedule's current tenant authorization.

    Pre-snapshot fallback only: archives generated before the scope snapshot
    existed keep their old behavior. New archives are validated against the
    archived generation scope instead.
    """
    scope = _resolve_report_scope(sched)
    if scope is None:
        return False
    active_tenant, filter_tenants = scope
    if not filter_tenants and _approved_scope_tenants_removed(sched):
        return False
    if _scope_requires_authorization(active_tenant, filter_tenants):
        return _resolve_scope_authorization(sched, active_tenant, filter_tenants) is not None
    return True


def _retry_scope_is_authorized(sched, archive):
    """Re-validate the ARCHIVED generation scope before any redelivery.

    A scope change must not legitimize an older export: the retry proceeds
    only while the schedule still resolves to the archived generation's
    active tenant and, for a cross-tenant archive, while a current, durable
    approval still covers every tenant whose data the retained file contains.
    Reducing the scope, revoking the approval, or losing the approving
    principal's permission all refuse the redelivery exactly like generation
    would have refused under the archived scope.
    """
    snapshot = archive.generation_scope if isinstance(getattr(archive, "generation_scope", None), dict) else {}
    if not snapshot:
        return _retry_current_scope_is_authorized(sched)
    scope = _resolve_report_scope(sched)
    if scope is None:
        return False
    active_tenant, _filter_tenants = scope
    if snapshot.get("active_tenant_id") != getattr(active_tenant, "pk", None):
        return False
    if not snapshot.get("cross_tenant"):
        return True
    return _archived_cross_tenant_scope_is_authorized(sched, snapshot)


def _archived_cross_tenant_scope_is_authorized(sched, snapshot):
    """Whether a current approval still covers the archived cross-tenant data.

    The archived file contains the archived active tenant plus every filter
    tenant; all of them must still be live and covered by an unrevoked
    approval whose authorized scope is a superset, and the approving principal
    must still hold the cross-tenant permission on each of them.
    """
    try:
        required_tenant_ids = sorted({int(tenant_id) for tenant_id in snapshot.get("data_tenant_ids") or []})
    except (TypeError, ValueError):
        return False
    if not required_tenant_ids:
        return False
    authorization = _load_scope_authorization(sched)
    if authorization is None or getattr(authorization, "revoked_at", None) is not None:
        return False
    authorized_scope = _parse_authorized_scope(authorization)
    if authorized_scope is None or not set(required_tenant_ids).issubset(set(authorized_scope)):
        return False
    principal = _resolve_authorized_principal(authorization)
    if principal is None:
        return False
    # inline import: heavy-import: organization models are only needed for tenant resolution
    from django.apps import apps

    Tenant = apps.get_model("organization", "Tenant")
    required_tenants = list(Tenant._base_manager.filter(pk__in=required_tenant_ids, deleted_at__isnull=True))
    if len(required_tenants) != len(required_tenant_ids):
        return False
    return _principal_can_authorize_scope(principal, required_tenants)


def _output_from_archive(sched, archive):
    """Rebuild the delivered output from the retained archive file."""
    if archive.file is None:
        return None
    field_file = archive.file.file
    field_file.open()
    try:
        content = field_file.read()
    finally:
        field_file.close()
    if archive.format == ScheduledReport.FORMAT_HTML:
        return _ReportOutput(email_body=content.decode("utf-8"))
    return _ReportOutput(
        email_body=_attachment_email_body(
            archive.format.upper(),
            sched.report.name,
            timestamp=archive.generated_at,
            disclosure_text=archive.disclosure_text,
        ),
        attachment_content=content,
        attachment_filename=archive.file.name,
        attachment_mime=archive.file.mime_type or "application/octet-stream",
    )


def _claim_retry(archive):
    """Take the exclusive retry claim; ``None`` when another attempt holds it.

    Mirrors the alert-dispatch claim lease: the claim is an atomic conditional
    UPDATE, so two parallel ``Retry delivery`` requests can never both read
    the same failed targets and both deliver, and a claim left behind by an
    interrupted request is recoverable once the lease expires.
    """
    token = uuid.uuid4().hex
    now = timezone.now()
    expires_at = now + RETRY_CLAIM_LEASE
    claimed = (
        ReportGenerationArchive._base_manager.filter(pk=archive.pk)
        .filter(Q(retry_claim_expires_at__isnull=True) | Q(retry_claim_expires_at__lt=now))
        .update(retry_claim_token=token, retry_claim_expires_at=expires_at)
    )
    if not claimed:
        return None
    archive.retry_claim_token = token
    archive.retry_claim_expires_at = expires_at
    return token


def _release_retry_claim(archive, token, *, delivery_targets=None, delivery_status=""):
    """Release the claim, writing the outcome only while the token still owns it.

    Returns ``False`` when the claim was superseded (the lease expired and a
    newer attempt took over); the caller then leaves the newer state in place
    instead of clobbering it with a stale ledger.
    """
    updates = {"retry_claim_token": "", "retry_claim_expires_at": None}
    if delivery_targets is not None:
        updates["delivery_targets"] = delivery_targets
        updates["delivery_status"] = delivery_status
    return bool(ReportGenerationArchive._base_manager.filter(pk=archive.pk, retry_claim_token=token).update(**updates))


def _recorded_detail(target, key, default=None):
    """Read one recorded detail from a ledger entry (``None`` when absent)."""
    details = target.get("details")
    if isinstance(details, dict):
        return details.get(key, default)
    return default


def _retry_channel_body(sched, archive):
    body = _(
        "Scheduled report '%(name)s' was generated on %(timestamp)s UTC and is being redelivered.\nFormat: %(format)s"
    ) % {
        "name": sched.name,
        "timestamp": f"{archive.generated_at:%Y-%m-%d %H:%M:%S}",
        "format": sched.format.upper(),
    }
    if archive.disclosure_text:
        body = f"{body}\n{archive.disclosure_text}"
    return body


def _retry_email_target(sched, output, recipients):
    """Re-attempt the aggregate email target; returns (ok, error_token)."""
    if output is None:
        return False, "retry.no_retained_output"
    if not recipients:
        return False, "email.no_recipients"
    try:
        delivered = _deliver_report_email(sched, sched.report, output, recipients)
    # broad except: boundary-isolation: SMTP providers expose implementation-specific delivery failures
    except Exception as error:
        logger.error(
            "Retried scheduled report email delivery failed",
            extra={
                "operation": "reports.email_delivery",
                "scheduled_report_id": getattr(sched, "pk", None),
                "exception_type": type(error).__name__,
            },
        )
        return False, "email.delivery_failed"
    if delivered:
        return True, ""
    return False, "email.delivery_rejected"


def _retry_channel_target(sched, archive, target_key, target=None):
    """Re-attempt one recorded channel target; returns (ok, error_token).

    The channel must still be attached to the schedule, still exist, and still
    be enabled: a detached or disabled channel is never contacted, mirroring
    the dispatch rule that disabled channels are skipped. The original
    notification payload recorded with the failed attempt is re-sent, so the
    redelivery matches the first attempt (summary cards included) instead of a
    reduced reconstruction; entries without a recorded payload fall back to
    the reconstruction.
    """
    try:
        channel_id = int(target_key.split(":", 1)[1])
    except (IndexError, ValueError):
        return False, "channel.missing"
    channel = NotificationChannel._base_manager.filter(pk=channel_id, deleted_at__isnull=True).first()
    if channel is None:
        return False, "channel.missing"
    through = ScheduledReport.channels.through
    if not through._base_manager.filter(scheduledreport_id=sched.pk, notificationchannel_id=channel.pk).exists():
        return False, "channel.detached"
    if not channel.enabled:
        return False, "channel.disabled"
    recorded_payload = _recorded_detail(target or {}, "payload", None)
    if isinstance(recorded_payload, dict) and recorded_payload.get("body"):
        subject = recorded_payload.get("subject") or _retry_subject(sched)
        body = recorded_payload["body"]
    else:
        subject = _retry_subject(sched)
        body = _retry_channel_body(sched, archive)
    try:
        delivered = send_notification_to_channel(channel, subject, body)
    # broad except: boundary-isolation: channel integrations may raise implementation-specific failures
    except Exception as error:
        logger.error(
            "Retried scheduled report channel delivery failed",
            extra={
                "operation": "reports.channel_delivery",
                "channel_id": channel.pk,
                "exception_type": type(error).__name__,
            },
        )
        return False, "channel.delivery_failed"
    if delivered:
        return True, ""
    return False, "channel.delivery_rejected"


def _retry_subject(sched):
    return _("[Scheduled Report] %(name)s") % {"name": sched.name}


def _complete_retry_claim(sched, archive, token, ledger):
    """Close a retry claim with the final ledger state.

    The write is fenced to the owning token; when the claim was superseded
    (the lease expired and a newer attempt took over), the newer state is left
    in place and only a warning is recorded.
    """
    all_ok = all(target.get("status") == "ok" for target in ledger)
    any_ok = any(target.get("status") == "ok" for target in ledger)
    new_status = "success" if all_ok else ("partial" if any_ok else "failed")
    if not _release_retry_claim(archive, token, delivery_targets=ledger, delivery_status=new_status):
        logger.warning(
            "Retry completion was superseded by a newer claim; newer state is left in place",
            extra={"operation": "reports.delivery_retry", "scheduled_report_id": sched.pk},
        )
        return False
    archive.delivery_status = new_status
    archive.delivery_targets = ledger
    if sched.last_status != new_status:
        sched.last_status = new_status
        sched.save(update_fields=["last_status"])
    return True


def _retry_failed_target(sched, archive, output, target):
    """Re-attempt one recorded failed target; returns ``(ok, error_token)``."""
    target_key = target.get("target", "")
    if target_key == DELIVERY_TARGET_EMAIL:
        recipients = _recorded_detail(target, "recipients") or []
        return _retry_email_target(sched, output, recipients)
    if target_key.startswith("channel:"):
        return _retry_channel_target(sched, archive, target_key, target)
    return False, "retry.unknown_target"


def retry_failed_deliveries(sched):
    """Re-attempt only the failed targets of the newest archived run.

    This is the recovery path of the frozen V1 contract: prior successful
    sends are never repeated (targets whose recorded outcome was already ok
    are not contacted again), the archived generation scope is re-validated
    before anything leaves the system (a later scope change or revocation
    must not legitimize an older export), a paused schedule is refused, the
    recorded original targets and payloads are replayed, and a lease-fenced
    claim makes two parallel attempts deliver exactly once. Without a
    retained archive output there is nothing to redeliver — ``Run now``
    re-runs the schedule instead.
    """
    archive = ReportGenerationArchive._base_manager.filter(scheduled_report=sched).order_by("-generated_at").first()
    if archive is None or archive.file is None:
        return _RetryOutcome("retry.no_archive")
    failed_targets = [target for target in (archive.delivery_targets or []) if target.get("status") != "ok"]
    if not failed_targets:
        return _RetryOutcome("retry.no_recorded_failures")
    if not sched.is_active:
        logger.warning(
            "Retry delivery refused: the schedule is inactive",
            extra={"operation": "reports.delivery_retry", "scheduled_report_id": sched.pk},
        )
        return _RetryOutcome("retry.inactive")
    if not _retry_scope_is_authorized(sched, archive):
        logger.warning(
            "Retry delivery refused: the archived generation scope is no longer authorized",
            extra={"operation": "reports.delivery_retry", "scheduled_report_id": sched.pk},
        )
        return _RetryOutcome("retry.scope_unauthorized")

    output = _output_from_archive(sched, archive)
    if output is None:
        return _RetryOutcome("retry.no_retained_output")
    token = _claim_retry(archive)
    if token is None:
        logger.warning(
            "Retry delivery refused: another attempt already holds the delivery claim",
            extra={"operation": "reports.delivery_retry", "scheduled_report_id": sched.pk},
        )
        return _RetryOutcome("retry.in_progress")

    retried = 0
    still_failed = 0
    for target in failed_targets:
        ok, error_token = _retry_failed_target(sched, archive, output, target)
        retried += 1
        target["retried"] = True
        if ok:
            target["status"] = "ok"
            target["error"] = ""
        else:
            target["error"] = error_token
            still_failed += 1

    ledger = list(archive.delivery_targets or [])
    _complete_retry_claim(sched, archive, token, ledger)
    logger.info(
        "Retried scheduled report deliveries",
        extra={
            "operation": "reports.delivery_retry",
            "scheduled_report_id": sched.pk,
            "retried": retried,
            "still_failed": still_failed,
        },
    )
    failed_after = [target for target in ledger if target.get("status") != "ok"]
    detail = "; ".join(
        f"{target.get('label') or target.get('target')}: {target.get('error')}" for target in failed_after
    )
    return _RetryOutcome(
        "retry.completed" if still_failed == 0 else "retry.partial",
        retried=retried,
        still_failed=still_failed,
        detail=detail,
    )
