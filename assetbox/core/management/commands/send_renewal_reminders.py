from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from core.models import Notification, EmailSettings
from core.events import send_notification
from subscriptions.models import Subscription


class Command(BaseCommand):
    help = 'Send renewal reminders for subscriptions due for renewal within 30 days.'

    def handle(self, *args, **options):
        email_config = EmailSettings.load()
        if not email_config or not email_config.enabled:
            self.stdout.write(self.style.WARNING('Email notifications are disabled. Skipping.'))
            return

        today = timezone.now().date()
        deadline = today + timedelta(days=30)

        upcoming = Subscription.objects.filter(
            renewal_date__lte=deadline,
            renewal_date__gte=today,
            status='active',
        ).select_related('provider')

        count = 0
        for sub in upcoming:
            days_left = (sub.renewal_date - today).days
            subject = f'Renewal Reminder: {sub.provider.name} - {sub.name} ({days_left} days)'
            body = (
                f'Subscription: {sub.provider.name} - {sub.name}\n'
                f'Renewal Date: {sub.renewal_date}\n'
                f'Cost: {sub.renewal_cost or "N/A"}\n'
                f'Auto-Renewal: {"Yes" if sub.auto_renewal else "No"}'
            )
            Notification.objects.create(
                user=None,
                subject=subject,
                message=body,
                level='warning',
                target_url=sub.get_absolute_url(),
            )
            send_notification(subject, body)
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Sent {count} renewal reminders.'))
