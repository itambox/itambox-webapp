import logging
import io
import csv
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.template import Template, Context
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import EmailSettings
from core.csv_utils import csv_safe, safe_csv_filename
from core.events import send_notification_to_channel
from core.tasks.context import TaskContext
from extras.models import FileAttachment, ScheduledReport, ReportTemplate, ReportGenerationArchive
from core.reports import compile_report_context, get_polished_system_html_template

logger = logging.getLogger(__name__)

def generate_scheduled_report_task(scheduled_report_id):
    """
    Background task to compile report data (Asset Summary, License Utilization, or Subscription Renewals),
    render it dynamically based on Visual No-Code layout configurations (columns selection, grouping,
    styling presets) or HTML/Jinja2 custom templates, and email it to configured recipients.
    """
    try:
        sched = ScheduledReport.objects.get(pk=scheduled_report_id)
    except ScheduledReport.DoesNotExist:
        logger.error(f"ScheduledReport {scheduled_report_id} not found.")
        return False

    if not sched.is_active:
        logger.warning(f"ScheduledReport {sched.name} is inactive. Skipping.")
        return False

    # Resolve the active tenant for this generation run. TaskContext below binds
    # the tenant + change-logging contextvars (request_id/current_user) for the
    # whole body so every save inside (sched.last_run/last_status,
    # ReportGenerationArchive create/save) is recorded as an ObjectChange, and
    # restores the prior context on exit.
    active_tenant = sched.tenant or (sched.report.tenant if sched.report else None)

    with TaskContext(tenant_id=active_tenant.id if active_tenant else None, user_id=None):
        # Resolve multi-tenant filter scoping constellation
        filter_tenants = list(sched.filter_tenants.all())
        if not filter_tenants and sched.report:
            filter_tenants = list(sched.report.filter_tenants.all())

        # Refuse a cross-tenant report: with no owning tenant AND no explicit filter_tenants
        # the compiler spans EVERY tenant and emails the aggregate to the free-text recipients.
        # A global report must name its tenants via filter_tenants.
        if active_tenant is None and not filter_tenants:
            logger.error(
                "Scheduled report '%s' has no tenant scope (no tenant, no filter_tenants) — "
                "refusing to compile a cross-tenant report.", sched.name,
            )
            return

        logger.info(f"Generating scheduled report: {sched.name} (Format: {sched.format})")
        sched.last_run = timezone.now()
        sched.save()

        try:
            template = sched.report

            # 1. Compile report data using unified compiler helper
            headers, rows, summary_cards, grouped_data, chart_svg, context_data = compile_report_context(

                template, active_tenant=active_tenant, filter_tenants=filter_tenants
            )
            context_data['scheduled_report'] = sched

            # Determine metrics variables for legacy CSV/Jinja format matching
            total_assets = len(rows)
            try:
                acquisition_sum = sum(float(r[headers[8]].replace('$', '').replace(',', '')) for r in rows if len(headers) > 8 and headers[8] in r and r[headers[8]] != '-')
            except Exception:
                acquisition_sum = 0

            total_active = len(rows)
            try:
                total_monthly_spend = sum(float(r[headers[3]].replace('$', '').replace(',', '')) for r in rows if len(headers) > 3 and headers[3] in r and r[headers[3]] != '-')
            except Exception:
                total_monthly_spend = 0

            # 2. Render HTML Email Body
            email_body = ""
            attachment_content = None
            attachment_filename = ""
            attachment_mime = ""

            if sched.format == ScheduledReport.FORMAT_HTML:
                if template.advanced_mode and template.template_content.strip():
                    try:
                        # inline import: defer optional/heavy jinja2 dependency to
                        # the advanced-mode branch that actually renders templates.
                        from jinja2.sandbox import SandboxedEnvironment
                        env = SandboxedEnvironment()
                        jinja_template = env.from_string(template.template_content)
                        if template.report_type == ReportTemplate.REPORT_TYPE_ASSET_SUMMARY:
                            context_data.update({
                                'total_assets': total_assets,
                                'acquisition_sum': acquisition_sum,
                                'location_distribution': [{'location': k, 'count': len(v)} for k, v in grouped_data.items()]
                            })
                        email_body = jinja_template.render(context_data)
                    except Exception as je:
                        logger.exception(f"Jinja2 sandboxed render failed: {je}")
                        raise je
                else:
                    html_template_str = get_polished_system_html_template()
                    django_template = Template(html_template_str)
                    email_body = django_template.render(Context(context_data))

            elif sched.format == ScheduledReport.FORMAT_CSV:
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)

                if template.advanced_mode:
                    # Original/Legacy CSV format
                    if template.report_type == ReportTemplate.REPORT_TYPE_ASSET_SUMMARY:
                        writer.writerow(['Metric', 'Value'])
                        writer.writerow(['Total Hardware Assets', total_assets])
                        writer.writerow(['Total Acquisition Sum ($)', acquisition_sum])
                        writer.writerow([])
                        writer.writerow(['Location', 'Allocated Count'])
                        for k, v in grouped_data.items():
                            writer.writerow([csv_safe(k), len(v)])
                    elif template.report_type == ReportTemplate.REPORT_TYPE_LICENSE_UTILIZATION:
                        writer.writerow(['License', 'Software', 'Total Seats', 'Assigned Seats', 'Available Seats', 'Utilization Rate'])
                        for r in rows:
                            writer.writerow([csv_safe(r.get(_('License Name'))), csv_safe(r.get(_('Software'))), r.get(_('Total Seats')), r.get(_('Assigned Seats')), r.get(_('Available Seats')), r.get(_('Utilization Rate'))])
                    elif template.report_type == ReportTemplate.REPORT_TYPE_SUBSCRIPTION_RENEWALS:
                        writer.writerow(['Active Subscriptions', total_active])
                        writer.writerow(['Monthly Spend ($)', total_monthly_spend])
                        writer.writerow([])
                        writer.writerow(['Subscription', 'Provider', 'Billing Cycle', 'Cost', 'End Date'])
                        for r in rows:
                            writer.writerow([csv_safe(r.get(_('Subscription Name'))), csv_safe(r.get(_('Provider'))), csv_safe(r.get(_('Billing Cycle'))), r.get(_('Cost')), r.get(_('End Date'))])
                else:
                    # Dynamic visual-columns CSV format
                    writer.writerow(headers)
                    for r in rows:
                        writer.writerow([csv_safe(r.get(head, '-')) for head in headers])

                attachment_content = csv_buffer.getvalue()
                attachment_filename = f"{safe_csv_filename(template.name).lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.csv"
                attachment_mime = "text/csv"
                email_body = _("Please find attached the scheduled CSV report for '%(name)s' generated on %(timestamp)s UTC.") % {
                    'name': template.name,
                    'timestamp': f"{timezone.now():%Y-%m-%d %H:%M:%S}",
                }

            # 3. Local In-App File Archive saving
            archive_entry = None
            file_attach = None
            if getattr(sched, 'save_to_archive', True):
                archive_entry = ReportGenerationArchive.objects.create(
                    scheduled_report=sched,
                    format=sched.format,
                    status='running',
                    tenant=active_tenant
                )

                # Save the compiled report stream as a FileAttachment
                if sched.format == ScheduledReport.FORMAT_HTML:
                    content_bytes = email_body.encode('utf-8')
                    mime = 'text/html'
                    filename = f"{template.name.lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.html"
                else:
                    content_bytes = attachment_content.encode('utf-8') if isinstance(attachment_content, str) else attachment_content
                    mime = 'text/csv'
                    filename = attachment_filename

                content_file = ContentFile(content_bytes, name=filename)
                file_attach = FileAttachment.objects.create(
                    content_object=archive_entry,
                    file=content_file,
                    name=filename,
                    mime_type=mime
                )
                archive_entry.file = file_attach
                archive_entry.status = 'success'
                archive_entry.save()

            # 4. Deliver Email (optional, only if recipients is configured)
            email_sent = False
            if sched.recipients:
                email_config = EmailSettings.load()
                if not email_config or not email_config.enabled:
                    raise ValidationError(_("SMTP Outbound Email is disabled in settings."))

                recipient_list = [r.strip() for r in sched.recipients.split(',') if r.strip()]
                if recipient_list:
                    email = EmailMessage(
                        subject=_("[Scheduled Report] %(name)s") % {'name': sched.name},
                        body=email_body,
                        from_email=email_config.from_address,
                        to=recipient_list,
                    )

                    if sched.format == ScheduledReport.FORMAT_HTML:
                        email.content_subtype = "html"
                    elif attachment_content:
                        email.attach(attachment_filename, attachment_content, attachment_mime)

                    email.send(fail_silently=False)
                    email_sent = True

            # 5. Dispatch to configured Notification Channels (email, in_app, Slack, Teams)
            report_subject = _("[Scheduled Report] %(name)s") % {'name': sched.name}
            report_body = _(
                "Scheduled report '%(name)s' was successfully generated "
                "on %(timestamp)s UTC.\n"
                "Format: %(format)s\n"
                "Total Assets: %(total_assets)s\n"
                "Acquisition Sum: $%(acquisition_sum)s"
            ) % {
                'name': sched.name,
                'timestamp': f"{timezone.now():%Y-%m-%d %H:%M:%S}",
                'format': sched.format.upper(),
                'total_assets': total_assets,
                'acquisition_sum': f"{acquisition_sum:,.2f}",
            }
            for channel in sched.channels.all():
                if not channel.enabled:
                    continue
                send_notification_to_channel(channel, report_subject, report_body)

            sched.last_status = "success"
            sched.save()
            logger.info(f"Scheduled report '{sched.name}' successfully processed.")
            return True

        except Exception as e:
            logger.exception(f"Error generating scheduled report '{sched.name}'")
            sched.last_status = f"failed: {str(e)}"
            sched.save()
            if archive_entry:
                archive_entry.status = 'failed'
                archive_entry.error_message = str(e)
                archive_entry.save()
            return False
