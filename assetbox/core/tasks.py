# ==============================================================================
# AssetBox Asynchronous Background Tasks
# ==============================================================================

import logging
import io
import zipfile
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Job, Notification, FileAttachment
from assetbox.registry import registry

logger = logging.getLogger(__name__)


def import_csv_task(job_id, rows_data, app_label, model_name, user_id):
    """
    Asynchronously imports parsed CSV/YAML rows into a target model
    using the dynamic BulkImportForm schema inside database transactions.
    """
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        user = None

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        logger.error(f"Job {job_id} not found during async import.")
        return

    job.mark_running()
    job.append_log("Initializing asynchronous import pipeline...")
    job.append_log(f"Target model: {app_label}.{model_name} | Row Count: {len(rows_data)}")

    try:
        # Resolve target model class dynamically
        model = ContentType.objects.get(app_label=app_label, model=model_name).model_class()
        if not model:
            raise ValidationError(f"Target model {app_label}.{model_name} could not be resolved.")

        # Dynamically instantiate import form class (similar to ObjectImportView)
        from django.views.generic import View
        from assetbox.views.generic import ObjectImportView
        
        # Instantiate views to leverage generic form resolution
        view_instance = ObjectImportView()
        view_instance.model = model
        ImportFormClass = view_instance.get_form_class()
        
        form = ImportFormClass()
        form._rows_data = rows_data

        job.append_log("Validating and importing records inside transaction...")
        
        with transaction.atomic():
            imported_count, errors = form.import_data()

        job.append_log(f"Import finished. Successfully imported: {imported_count} record(s).")
        
        if errors:
            job.append_log(f"Encountered {len(errors)} error(s) during processing:")
            for err in errors:
                job.append_log(f" - {err}")
            
            # If everything failed, mark job as failed, otherwise partial success
            if imported_count == 0:
                job.mark_failed("All records failed to import due to validation errors.")
                Notification.objects.create(
                    user=user,
                    subject=f"Bulk Import Failed",
                    message=f"Failed to import CSV/YAML data to {model._meta.verbose_name_plural}. View job logs for details.",
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk)
                )
                return

        job.mark_completed(result={
            'imported': imported_count,
            'failed': len(errors),
            'total': len(rows_data)
        })

        Notification.objects.create(
            user=user,
            subject=f"Bulk Import Complete",
            message=f"Successfully imported {imported_count} record(s) to {model._meta.verbose_name_plural}.",
            level=Notification.LEVEL_SUCCESS,
            target_url=reverse_job_detail(job.pk)
        )

    except Exception as e:
        logger.exception("Exception during async import task")
        job.mark_failed(str(e))
        Notification.objects.create(
            user=user,
            subject=f"Bulk Import Error",
            message=f"A system exception occurred during the import: {str(e)}",
            level=Notification.LEVEL_DANGER,
            target_url=reverse_job_detail(job.pk)
        )


def bulk_checkout_task(job_id, asset_pks, target_type_str, target_pk, user_id, notes, expected_checkin_date=None):
    """
    Asynchronously executes bulk checkout operations on selected hardware Assets
    utilizing select_for_update row-level locking to prevent race anomalies.
    """
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        user = None

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        logger.error(f"Job {job_id} not found during async checkout.")
        return

    job.mark_running()
    job.append_log("Initializing asynchronous bulk checkout pipeline...")
    job.append_log(f"Assets to process: {len(asset_pks)}")

    try:
        # Resolve target assignment GFK
        target_model = ContentType.objects.get(
            app_label='organization' if target_type_str == 'assetholder' else 'assets' if target_type_str == 'asset' else 'organization',
            model=target_type_str
        ).model_class()
        
        target = target_model.objects.get(pk=target_pk)
        job.append_log(f"Checkout target assignee: {str(target)}")

        from assets.models import Asset
        from assets.services import checkout_asset
        
        success_count = 0
        failure_count = 0
        
        for pk in asset_pks:
            try:
                # Concurrency-safe lookup
                with transaction.atomic():
                    asset = Asset.objects.select_for_update().get(pk=pk)
                    checkout_asset(
                        asset=asset,
                        target=target,
                        user=user,
                        notes=notes,
                        expected_checkin=expected_checkin_date
                    )
                    success_count += 1
                    job.append_log(f" - Asset {asset.asset_tag} ({asset.name}) checked out successfully.")
            except Exception as ex:
                failure_count += 1
                job.append_log(f" - Failed to checkout Asset PK {pk}: {str(ex)}")

        job.append_log(f"Bulk checkout execution finished. Successes: {success_count} | Failures: {failure_count}")

        if success_count == 0:
            job.mark_failed("All asset checkouts failed.")
            Notification.objects.create(
                user=user,
                subject="Bulk Checkout Failed",
                message="All hardware checkouts failed. View logs for error tracebacks.",
                level=Notification.LEVEL_DANGER,
                target_url=reverse_job_detail(job.pk)
            )
            return

        job.mark_completed(result={
            'checked_out': success_count,
            'failed': failure_count,
            'total': len(asset_pks)
        })

        Notification.objects.create(
            user=user,
            subject="Bulk Checkout Complete",
            message=f"Successfully checked out {success_count} asset(s).",
            level=Notification.LEVEL_SUCCESS,
            target_url=reverse_job_detail(job.pk)
        )

    except Exception as e:
        logger.exception("Exception during bulk checkout task")
        job.mark_failed(str(e))
        Notification.objects.create(
            user=user,
            subject="Bulk Checkout Error",
            message=f"A system exception occurred during the checkout: {str(e)}",
            level=Notification.LEVEL_DANGER,
            target_url=reverse_job_detail(job.pk)
        )


def generate_label_batch_task(job_id, asset_pks, label_format, user_id):
    """
    Asynchronously generates QR-codes/barcodes for selected assets,
    packages them into a ZIP archive, and attaches it directly to the Job.
    """
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        user = None

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        logger.error(f"Job {job_id} not found during async label printing.")
        return

    job.mark_running()
    job.append_log("Starting label batch generation...")
    job.append_log(f"Format: {label_format} | Total assets: {len(asset_pks)}")

    try:
        from assets.models import Asset
        assets = Asset.objects.filter(pk__in=asset_pks)
        
        # Build ZIP archive in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for asset in assets:
                try:
                    # Generate dynamic label graphic (QR or barcode)
                    img_data = generate_single_label_graphic(asset, label_format)
                    filename = f"label_{asset.asset_tag}_{label_format}.png"
                    zip_file.writestr(filename, img_data)
                    job.append_log(f" - Rendered label for {asset.asset_tag}")
                except Exception as ex:
                    job.append_log(f" - Error rendering label for PK {asset.pk}: {str(ex)}")

        zip_buffer.seek(0)
        
        # Save archive directly as a FileAttachment on the Job model
        ct = ContentType.objects.get_for_model(Job)
        attachment = FileAttachment.objects.create(
            model=ct,
            object_id=job.pk,
            name=f"labels_batch_{job.pk}.zip",
            mime_type="application/zip"
        )
        # Direct save of bytes
        from django.core.files.base import ContentFile
        attachment.file.save(f"labels_batch_{job.pk}.zip", ContentFile(zip_buffer.getvalue()))
        attachment.save()

        job.append_log(f"ZIP package generated and saved successfully: {attachment.file.name}")
        job.mark_completed(result={
            'file_name': attachment.name,
            'download_url': attachment.file.url
        })

        Notification.objects.create(
            user=user,
            subject="Label Generation Complete",
            message=f"Successfully generated label batch zip for {assets.count()} asset(s). Click to download.",
            level=Notification.LEVEL_SUCCESS,
            target_url=attachment.file.url
        )

    except Exception as e:
        logger.exception("Exception during label batch generation task")
        job.mark_failed(str(e))
        Notification.objects.create(
            user=user,
            subject="Label Generation Failed",
            message=f"An error occurred during barcode rendering: {str(e)}",
            level=Notification.LEVEL_DANGER,
            target_url=reverse_job_detail(job.pk)
        )


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
        
        for sub in expiring_subs:
            # Generate admin broadcast alert
            Notification.objects.create(
                user=None,  # Global broadcast alert
                subject="Subscription Renewal Due",
                message=f"Subscription '{sub.name}' is due to renew on {sub.renewal_date:%Y-%m-%d}. Scoped cost: {sub.renewal_cost}.",
                level=Notification.LEVEL_WARNING,
                target_url=sub.get_absolute_url()
            )
            job.append_log(f" - Generated renewal reminder for: {sub.name}")

        # 2. Expiring Warranties (within 30 days)
        expiring_warranties = Asset.objects.filter(
            warranty_expiration__range=[now.date(), thirty_days_later.date()]
        )
        warranty_alert_count = expiring_warranties.count()
        for asset in expiring_warranties:
            Notification.objects.create(
                user=None,
                subject="Hardware Warranty Expiring",
                message=f"Asset {asset.asset_tag} ({asset.name}) warranty expires on {asset.warranty_expiration:%Y-%m-%d}.",
                level=Notification.LEVEL_WARNING,
                target_url=asset.get_absolute_url()
            )
            job.append_log(f" - Generated warranty alert for: {asset.asset_tag}")

        job.append_log(f"Generated {warranty_alert_count} hardware warranty alert(s).")
        job.mark_completed(result={
            'expiring_subscriptions': expiring_subs.count(),
            'expiring_warranties': warranty_alert_count
        })

    except Exception as e:
        logger.exception("Exception during scheduled nightly checks")
        job.mark_failed(str(e))


# ==============================================================================
# Helper Utilities
# ==============================================================================

def generate_single_label_graphic(asset, label_format):
    """
    Renders QR code or Barcode PNG bytes for the given asset.
    """
    import io
    buffer = io.BytesIO()
    
    if label_format == 'qr':
        import segno
        # Generate clean QR code
        qr = segno.make_qr(f"assetbox://asset/{asset.pk}")
        qr.save(buffer, kind='png', scale=10)
    else:
        import barcode
        from barcode.writer import ImageWriter
        # Generate barcode
        CODING = barcode.get_barcode_class('code128')
        # Clean text
        code = CODING(asset.asset_tag or str(asset.pk), writer=ImageWriter())
        code.write(buffer)

    return buffer.getvalue()


def reverse_job_detail(job_id):
    """
    Helper to resolve job detail URL safely.
    """
    try:
        from django.urls import reverse
        return reverse('job_detail', kwargs={'pk': job_id})
    except Exception:
        return f"/jobs/{job_id}/"


def generate_scheduled_report_task(scheduled_report_id):
    """
    Background task to compile report data (Asset Summary, License Utilization, or Subscription Renewals),
    render it dynamically based on Visual No-Code layout configurations (columns selection, grouping,
    styling presets) or HTML/Jinja2 custom templates, and email it to configured recipients.
    """
    from core.models import ScheduledReport, ReportTemplate, EmailSettings
    from django.core.mail import EmailMessage
    from django.template import Template, Context
    from django.utils.translation import gettext as _
    import csv
    import io
    
    try:
        sched = ScheduledReport.objects.get(pk=scheduled_report_id)
    except ScheduledReport.DoesNotExist:
        logger.error(f"ScheduledReport {scheduled_report_id} not found.")
        return False
        
    if not sched.is_active:
        logger.warning(f"ScheduledReport {sched.name} is inactive. Skipping.")
        return False

    # Bind thread-local active tenant context for background generation
    from core.managers import set_current_tenant
    active_tenant = sched.tenant or (sched.report.tenant if sched.report else None)
    set_current_tenant(active_tenant)

    # Resolve multi-tenant filter scoping constellation
    filter_tenants = list(sched.filter_tenants.all())
    if not filter_tenants and sched.report:
        filter_tenants = list(sched.report.filter_tenants.all())
        
    logger.info(f"Generating scheduled report: {sched.name} (Format: {sched.format})")
    sched.last_run = timezone.now()
    sched.save()
    
    try:
        template = sched.report
        
        # 1. Compile report data using unified compiler helper
        from core.reports_charts import compile_report_context, get_polished_system_html_template
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
                    logger.error(f"Jinja2 sandboxed render failed: {je}. Falling back to Django template engine.")
                    django_template = Template(template.template_content)
                    email_body = django_template.render(Context(context_data))
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
                        writer.writerow([k, len(v)])
                elif template.report_type == ReportTemplate.REPORT_TYPE_LICENSE_UTILIZATION:
                    writer.writerow(['License', 'Software', 'Total Seats', 'Assigned Seats', 'Available Seats', 'Utilization Rate'])
                    for r in rows:
                        writer.writerow([r.get(_('License Name')), r.get(_('Software')), r.get(_('Total Seats')), r.get(_('Assigned Seats')), r.get(_('Available Seats')), r.get(_('Utilization Rate'))])
                elif template.report_type == ReportTemplate.REPORT_TYPE_SUBSCRIPTION_RENEWALS:
                    writer.writerow(['Active Subscriptions', total_active])
                    writer.writerow(['Monthly Spend ($)', total_monthly_spend])
                    writer.writerow([])
                    writer.writerow(['Subscription', 'Provider', 'Billing Cycle', 'Cost', 'End Date'])
                    for r in rows:
                        writer.writerow([r.get(_('Subscription Name')), r.get(_('Provider')), r.get(_('Billing Cycle')), r.get(_('Cost')), r.get(_('End Date'))])
            else:
                # Dynamic visual-columns CSV format
                writer.writerow(headers)
                for r in rows:
                    writer.writerow([r.get(head, '-') for head in headers])
                    
            attachment_content = csv_buffer.getvalue()
            attachment_filename = f"{template.name.lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.csv"
            attachment_mime = "text/csv"
            email_body = f"Please find attached the scheduled CSV report for '{template.name}' generated on {timezone.now():%Y-%m-%d %H:%M:%S} UTC."
            
        # 3. Local In-App File Archive saving
        archive_entry = None
        file_attach = None
        if getattr(sched, 'save_to_archive', True):
            from core.models import ReportGenerationArchive, FileAttachment
            archive_entry = ReportGenerationArchive.objects.create(
                scheduled_report=sched,
                format=sched.format,
                status='running',
                tenant=active_tenant
            )
            
            # Save the compiled report stream as a FileAttachment
            from django.core.files.base import ContentFile
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
                raise ValidationError("SMTP Outbound Email is disabled in settings.")
                
            recipient_list = [r.strip() for r in sched.recipients.split(',') if r.strip()]
            if recipient_list:
                email = EmailMessage(
                    subject=f"[Scheduled Report] {sched.name}",
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

        # 5. Dispatch to custom Notification Channels (Slack, Teams, Webhook, In-App)
        for channel in sched.channels.all():
            if not channel.enabled:
                continue
            
            from core.models import NotificationChannel
            if channel.channel_type == NotificationChannel.TYPE_WEBHOOK:
                payload = {
                    'report_name': sched.name,
                    'report_template': template.name,
                    'generated_at': timezone.now().isoformat(),
                    'metrics': {
                        'total_assets': total_assets,
                        'acquisition_sum': acquisition_sum,
                        'total_active': total_active,
                        'total_monthly_spend': total_monthly_spend,
                    },
                    'headers': headers,
                    'rows': [{head: r.get(head, '-') for head in headers} for r in rows]
                }
                import requests
                try:
                    res = requests.post(channel.config.get('url', ''), json=payload, timeout=10)
                    res.raise_for_status()
                except Exception as e:
                    logger.error(f"Failed to post webhook report to {channel.name}: {e}")
                    
            elif channel.channel_type == NotificationChannel.TYPE_IN_APP:
                from core.models import Notification
                target_url = f"/attachments/file/download/{file_attach.pk}/" if file_attach else None
                Notification.objects.create(
                    user=None,
                    subject=f"[Scheduled Report] {sched.name}",
                    message=f"Scheduled report '{sched.name}' was successfully generated. Format: {sched.format.upper()}.",
                    target_url=target_url
                )
                
            else:
                # Slack/Teams
                slack_msg = (
                    f"**Scheduled Report: {sched.name}**\n"
                    f"Generated successfully on {timezone.now():%Y-%m-%d %H:%M:%S} UTC.\n"
                    f"**Format**: {sched.format.upper()}\n"
                    f"**Total Assets**: {total_assets}\n"
                    f"**Acquisition Sum**: ${acquisition_sum:,.2f}"
                )
                from core.events import send_notification_to_channel
                send_notification_to_channel(channel, f"[Scheduled Report] {sched.name}", slack_msg)

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
    finally:
        from core.managers import set_current_tenant
        set_current_tenant(None)


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
                from inventory.models import Accessory, Consumable, AccessoryStock, AccessoryAssignment, ConsumableStock, ConsumableAssignment
                from components.models import Component, ComponentStock, ComponentAllocation
                
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
                    comp_allocs_sub = comp_allocs_sub.filter(asset__tenant=rule.tenant)
                comp_allocs_sum = Subquery(
                    comp_allocs_sub.values('component').annotate(total=Sum('qty_allocated')).values('total')
                )
                
                comp_qs = Component.objects.filter(deleted_at__isnull=True).annotate(
                    annotated_total_stock=Coalesce(comp_stocks_sum, 0),
                    annotated_allocated_stock=Coalesce(comp_allocs_sum, 0)
                )
                
                for comp in comp_qs:
                    total_stock = comp.annotated_total_stock
                    allocated = comp.annotated_allocated_stock
                    available = total_stock - allocated
                    
                    threshold = comp.min_stock_level if (comp.min_stock_level and comp.min_stock_level > 0) else rule.threshold_value
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
            set_current_tenant(None)
                
    logger.info(f"Alert evaluation complete. Triggered {alerts_triggered_count} fresh alert(s).")
    return alerts_triggered_count
