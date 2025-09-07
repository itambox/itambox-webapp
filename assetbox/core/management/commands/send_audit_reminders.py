from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta

from assets.models import Asset
from core.models import Notification, EmailSettings
from core.events import send_notification


class Command(BaseCommand):
    help = 'Send email alerts for assets overdue for audit.'

    def handle(self, *args, **options):
        email_config = EmailSettings.load()
        if not email_config or not email_config.enabled:
            self.stdout.write(self.style.WARNING('Email notifications are disabled. Skipping.'))
            return

        today = timezone.now().date()
        audit_interval_days = getattr(settings, 'AUDIT_REMINDER_INTERVAL_DAYS', 365)
        deadline = today - timedelta(days=audit_interval_days)

        overdue_assets = Asset.objects.filter(
            last_audited__lte=deadline,
        ).select_related('asset_type', 'asset_type__manufacturer') | Asset.objects.filter(
            last_audited__isnull=True,
        ).select_related('asset_type', 'asset_type__manufacturer')

        overdue_assets = overdue_assets.distinct()

        count = 0
        for asset in overdue_assets:
            last_audited = asset.last_audited.strftime('%Y-%m-%d') if asset.last_audited else 'Never'
            days_since = 'N/A' if not asset.last_audited else (today - asset.last_audited).days

            subject = f'Audit Overdue: {asset.name} ({asset.asset_tag})'
            body = (
                f'Asset: {asset.name} ({asset.asset_tag})\n'
                f'Serial: {asset.serial_number or "N/A"}\n'
                f'Last Audited: {last_audited}\n'
                f'Days Since Last Audit: {days_since}\n'
                f'Location: {asset.location or "N/A"}'
            )

            Notification.objects.create(
                user=None,
                subject=subject,
                message=body,
                level='warning',
                target_url=asset.get_absolute_url(),
            )

            send_notification(subject, body)

            if email_config.from_address:
                try:
                    send_mail(
                        subject=subject,
                        message=body,
                        from_email=email_config.from_address,
                        recipient_list=[email_config.test_recipient or email_config.from_address],
                        fail_silently=True,
                    )
                except Exception:
                    self.stderr.write(f'Failed to send email for {asset}')

            count += 1

        self.stdout.write(self.style.SUCCESS(f'Sent {count} audit overdue reminders.'))
