import logging
from dataclasses import dataclass, field

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.translation import gettext as _

from core.context import get_current_user
from core.csv_utils import safe_csv_filename
from core.events import send_notification_to_channel
from core.features import report_designer_probe
from core.models import EmailSettings
from core.reports import build_report_context
from core.reports.rendering import render_report_csv, render_report_html
from core.tasks.context import TaskContext
from core.tasks.utils import TaskResult, TaskStatus, classify_task_error
from extras.models import (
    FileAttachment,
    ReportGenerationArchive,
    ScheduledReport,
    ScheduledReportScopeAuthorization,
)

logger = logging.getLogger(__name__)


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

    @property
    def status(self):
        if not self.failures:
            return "success"
        return "partial" if self.succeeded else "failed"

    def record_success(self):
        self.attempted += 1
        self.succeeded += 1

    def record_failure(self, failure):
        self.attempted += 1
        self.failures.append(str(failure))

    def merge(self, other):
        self.attempted += other.attempted
        self.succeeded += other.succeeded
        self.failures.extend(other.failures)


def _report_filename(template, extension):
    return f"{safe_csv_filename(template.name).lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.{extension}"


def _attachment_email_body(format_name, template):
    return _("Please find attached the scheduled %(format)s report for '%(name)s' generated on %(timestamp)s UTC.") % {
        "format": format_name,
        "name": template.name,
        "timestamp": f"{timezone.now():%Y-%m-%d %H:%M:%S}",
    }


def _render_report_output(sched, template, headers, rows, context_data):
    if sched.format == ScheduledReport.FORMAT_HTML:
        return _ReportOutput(email_body=_render_report_html(context_data, template))

    if sched.format == ScheduledReport.FORMAT_PDF:
        # inline import: heavy-import: PDF exporter is needed only for PDF schedules
        from core.reports.exporters import PDF_MIME, report_pdf_bytes

        return _ReportOutput(
            email_body=_attachment_email_body("PDF", template),
            attachment_content=report_pdf_bytes(_render_report_html(context_data, template)),
            attachment_filename=_report_filename(template, "pdf"),
            attachment_mime=PDF_MIME,
        )

    if sched.format == ScheduledReport.FORMAT_XLSX:
        # inline import: heavy-import: spreadsheet exporter is needed only for XLSX schedules
        from core.reports.exporters import XLSX_MIME, report_xlsx_bytes

        return _ReportOutput(
            email_body=_attachment_email_body("XLSX", template),
            attachment_content=report_xlsx_bytes(headers, rows, sheet_title=template.name),
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
        )
        return _ReportOutput(
            email_body=_attachment_email_body("CSV", template),
            attachment_content=csv_content,
            attachment_filename=_report_filename(template, "csv"),
            attachment_mime="text/csv",
        )

    raise ValueError(f"Unsupported scheduled report format: {sched.format}")


def _archive_report_output(sched, template, output, active_tenant):
    if not getattr(sched, "save_to_archive", True):
        return None

    archive_entry = ReportGenerationArchive.objects.create(
        scheduled_report=sched,
        format=sched.format,
        status="running",
        tenant=active_tenant,
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


def _deliver_report_channels(sched, summary_cards, total_rows):
    report_subject = _("[Scheduled Report] %(name)s") % {"name": sched.name}
    card_lines = "\n".join("%s: %s" % (card.get("label"), card.get("value")) for card in (summary_cards or [])) or (
        _("Rows: %(n)s") % {"n": total_rows}
    )
    report_body = _(
        "Scheduled report '%(name)s' was successfully generated on %(timestamp)s UTC.\nFormat: %(format)s\n%(summary)s"
    ) % {
        "name": sched.name,
        "timestamp": f"{timezone.now():%Y-%m-%d %H:%M:%S}",
        "format": sched.format.upper(),
        "summary": card_lines,
    }
    outcome = _DeliveryOutcome()
    for channel in sched.channels.all():
        if not channel.enabled:
            continue
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
            outcome.record_failure("channel.delivery_failed")
        else:
            if delivered:
                outcome.record_success()
            else:
                logger.warning(
                    "Scheduled report channel reported delivery failure",
                    extra={"operation": "reports.channel_delivery", "channel_id": getattr(channel, "pk", None)},
                )
                outcome.record_failure("channel.delivery_rejected")
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
    if authorization is None:
        return None
    scope_tenant_ids = sorted({tenant.pk for tenant in filter_tenants})
    authorized_scope = _parse_authorized_scope(authorization)
    if authorized_scope is None or authorized_scope != scope_tenant_ids:
        return None
    principal = _resolve_authorized_principal(authorization)
    if principal is None or not _principal_can_authorize_scope(principal, filter_tenants):
        return None
    return principal.pk


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
        archive_entry = _archive_report_output(sched, template, output, active_tenant)
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
            delivery.record_failure("email.delivery_failed")
        else:
            if delivered:
                delivery.record_success()
            else:
                delivery.record_failure("email.delivery_rejected")

    delivery.merge(_deliver_report_channels(sched, summary_cards, len(rows)))
    if delivery.failures:
        delivery_detail = "\n".join(delivery.failures)
        if archive_entry:
            archive_entry.error_message = delivery_detail
            archive_entry.save(update_fields=["error_message"])
            sched.last_status = delivery.status
        else:
            sched.last_status = f"delivery_{delivery.status}: {delivery_detail}"[:50]
        sched.save()
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
            user_visible=True,
        )

    sched.last_status = "success"
    sched.save()
    logger.info(
        "Scheduled report successfully processed",
        extra={"operation": "reports.generate", "scheduled_report_id": getattr(sched, "pk", None)},
    )
    return TaskResult(TaskStatus.SUCCESS, "report.completed", user_visible=True)


def generate_scheduled_report_task(scheduled_report_id: int) -> TaskResult:
    """Compile and deliver one scheduled report inside a tenant-scoped task context."""
    try:
        sched = ScheduledReport.objects.get(pk=scheduled_report_id)
    except ScheduledReport.DoesNotExist:
        logger.error(
            "Scheduled report not found",
            extra={"operation": "reports.generate", "scheduled_report_id": scheduled_report_id},
        )
        return TaskResult(TaskStatus.TERMINAL, "report.not_found")

    if not report_designer_probe().active and not getattr(sched.report, "legacy_designer_grandfathered", False):
        logger.warning(
            "Report designer capability is inactive",
            extra={"operation": "reports.generate", "scheduled_report_id": sched.pk},
        )
        return TaskResult(TaskStatus.SKIPPED, "report.capability_inactive")

    if not sched.is_active:
        logger.warning(
            "Scheduled report is inactive",
            extra={"operation": "reports.generate", "scheduled_report_id": sched.pk},
        )
        return TaskResult(TaskStatus.SKIPPED, "report.inactive")

    scope = _resolve_report_scope(sched)
    if scope is None:
        return TaskResult(TaskStatus.TERMINAL, "report.scope_missing", user_visible=True)
    active_tenant, filter_tenants = scope
    if not filter_tenants:
        existing_authorization = ScheduledReportScopeAuthorization._base_manager.filter(
            scheduled_report=sched
        ).first()
        if existing_authorization and existing_authorization.scope_tenant_ids:
            # Soft-deleting a tenant strips it from filter M2M sets, so a
            # previously approved broad scope can silently collapse to the
            # owner tenant. Fail closed instead of substituting owner data.
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
