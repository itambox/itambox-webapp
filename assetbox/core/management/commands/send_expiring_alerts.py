from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta

from assets.models import Asset
from licenses.models import License
from core.models import Notification, EmailSettings
from core.events import send_notification


class Command(BaseCommand):
    help = 'Send email alerts for assets/licenses nearing warranty/expiration dates.'

    def handle(self, *args, **options):
        email_config = EmailSettings.load()
        if not email_config or not email_config.enabled:
            self.stdout.write(self.style.WARNING('Email notifications are disabled. Skipping.'))
            return

        today = timezone.now().date()
        warning_days = 30
        deadline = today + timedelta(days=warning_days)

        expiring_assets = Asset.objects.filter(
            warranty_expiration__lte=deadline,
            warranty_expiration__gte=today,
        ).select_related('asset_type__manufacturer')

        expiring_licenses = License.objects.filter(
            expiration_date__lte=deadline,
            expiration_date__gte=today,
        ).select_related('software')

        count = 0

        for asset in expiring_assets:
            self._send_alert(email_config, asset, 'asset_warranty', today)
            count += 1

        for license_obj in expiring_licenses:
            self._send_alert(email_config, license_obj, 'license_expiration', today)
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Sent {count} expiring alerts.'))

    def _send_alert(self, config, obj, alert_type, today):
        if alert_type == 'asset_warranty':
            subject = f'Warranty Expiring: {obj.name} ({obj.asset_tag})'
            body = (
                f'Asset: {obj.name} ({obj.asset_tag})\n'
                f'Serial: {obj.serial_number or "N/A"}\n'
                f'Warranty expires: {obj.warranty_expiration}\n'
                f'Days remaining: {(obj.warranty_expiration - today).days}'
            )
        else:
            subject = f'License Expiring: {obj.software.name} - {obj.name}'
            body = (
                f'License: {obj.software.name} ({obj.name})\n'
                f'Expires: {obj.expiration_date}\n'
                f'Days remaining: {(obj.expiration_date - today).days}'
            )

        Notification.objects.create(user=None, subject=subject, message=body, level='warning')

        send_notification(subject, body)

        if config.from_address:
            try:
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=config.from_address,
                    recipient_list=[config.test_recipient or config.from_address],
                    fail_silently=True,
                )
            except Exception:
                self.stderr.write(f'Failed to send email for {obj}')
