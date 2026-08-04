import csv
import io
import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.template import Context, Template
from django.utils import timezone
from django.utils.translation import gettext as _

from core.csv_utils import csv_safe, safe_csv_filename
from core.events import send_notification_to_channel
from core.models import EmailSettings
from core.reports import compile_report_context, get_polished_system_html_template
from core.tasks.context import TaskContext
from extras.models import FileAttachment, ReportGenerationArchive, ScheduledReport

logger = logging.getLogger(__name__)


@dataclass
class _ReportOutput:
    email_body: str
    attachment_content: bytes | str | None = None
    attachment_filename: str = ""
    attachment_mime: str = ""


def _report_filename(template, extension):
    return f"{safe_csv_filename(template.name).lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.{extension}"


def _render_report_html(context_data):
    return Template(get_polished_system_html_template()).render(Context(context_data))


def _attachment_email_body(format_name, template):
    return _("Please find attached the scheduled %(format)s report for '%(name)s' generated on %(timestamp)s UTC.") % {
        "format": format_name,
        "name": template.name,
        "timestamp": f"{timezone.now():%Y-%m-%d %H:%M:%S}",
    }


def _render_report_output(sched, template, headers, rows, context_data):
    if sched.format == ScheduledReport.FORMAT_HTML:
        return _ReportOutput(email_body=_render_report_html(context_data))

    if sched.format == ScheduledReport.FORMAT_PDF:
        # inline import: heavy-import: PDF exporter is needed only for PDF schedules
        from core.reports.exporters import PDF_MIME, report_pdf_bytes

        return _ReportOutput(
            email_body=_attachment_email_body("PDF", template),
            attachment_content=report_pdf_bytes(_render_report_html(context_data)),
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
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([csv_safe(row.get(header, "-")) for header in headers])
        return _ReportOutput(
            email_body=_attachment_email_body("CSV", template),
            attachment_content=csv_buffer.getvalue(),
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


def _deliver_report_email(sched, template, output):
    if not sched.recipients:
        return

    email_config = EmailSettings.load()
    if not email_config or not email_config.enabled:
        raise ValidationError(_("SMTP Outbound Email is disabled in settings."))

    recipient_list = [recipient.strip() for recipient in sched.recipients.split(",") if recipient.strip()]
    if not recipient_list:
        return

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


def _notify_report_channels(sched, summary_cards, total_rows):
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
    for channel in sched.channels.all():
        if channel.enabled:
            send_notification_to_channel(channel, report_subject, report_body)


def _resolve_report_scope(sched):
    active_tenant = sched.tenant or (sched.report.tenant if sched.report else None)
    filter_tenants = list(sched.filter_tenants.all())
    if not filter_tenants and sched.report:
        filter_tenants = list(sched.report.filter_tenants.all())
    if active_tenant is None and not filter_tenants:
        logger.error(
            "Scheduled report '%s' has no tenant scope (no tenant, no filter_tenants) — "
            "refusing to compile a cross-tenant report.",
            sched.name,
        )
        return None
    return active_tenant, filter_tenants


def _process_scheduled_report(sched, active_tenant, filter_tenants):
    archive_entry = None
    try:
        template = sched.report
        headers, rows, summary_cards, _grouped_data, _chart_svg, context_data = compile_report_context(
            template,
            active_tenant=active_tenant,
            filter_tenants=filter_tenants,
        )
        context_data["scheduled_report"] = sched
        output = _render_report_output(sched, template, headers, rows, context_data)
        archive_entry = _archive_report_output(sched, template, output, active_tenant)
        _deliver_report_email(sched, template, output)
        _notify_report_channels(sched, summary_cards, len(rows))
        sched.last_status = "success"
        sched.save()
        logger.info("Scheduled report '%s' successfully processed.", sched.name)
        return True
    # broad except: task-isolation: one scheduled report failure must not abort the worker batch
    except Exception as error:
        logger.exception("Error generating scheduled report '%s'", sched.name)
        sched.last_status = f"failed: {error}"
        sched.save()
        if archive_entry:
            archive_entry.status = "failed"
            archive_entry.error_message = str(error)
            archive_entry.save()
        return False


def generate_scheduled_report_task(scheduled_report_id: int) -> bool | None:
    """Compile and deliver one scheduled report inside a tenant-scoped task context."""
    try:
        sched = ScheduledReport.objects.get(pk=scheduled_report_id)
    except ScheduledReport.DoesNotExist:
        logger.error("ScheduledReport %s not found.", scheduled_report_id)
        return False

    if not sched.is_active:
        logger.warning("ScheduledReport %s is inactive. Skipping.", sched.name)
        return False

    scope = _resolve_report_scope(sched)
    if scope is None:
        return None
    active_tenant, filter_tenants = scope

    with TaskContext(tenant_id=active_tenant.id if active_tenant else None, user_id=None):
        logger.info("Generating scheduled report: %s (Format: %s)", sched.name, sched.format)
        sched.last_run = timezone.now()
        sched.save()
        return _process_scheduled_report(sched, active_tenant, filter_tenants)
