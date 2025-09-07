from django.core.management.base import BaseCommand
from django.db import models

from core.models import Notification, EmailSettings
from core.events import send_notification
from inventory.models import Accessory, Consumable


class Command(BaseCommand):
    help = 'Send low stock alerts for accessories and consumables below minimum quantity thresholds.'

    def handle(self, *args, **options):
        email_config = EmailSettings.load()
        if not email_config or not email_config.enabled:
            self.stdout.write(self.style.WARNING('Email notifications are disabled. Skipping.'))
            return

        low_accessories = Accessory.objects.filter(min_qty__gt=0)
        low_consumables = Consumable.objects.filter(min_qty__gt=0)

        count = 0

        for acc in low_accessories:
            available = acc.available
            if available >= acc.min_qty:
                continue
            subject = f'Low Stock: {acc.name} ({available}/{acc.min_qty} remaining)'
            body = f'{acc.manufacturer.name} {acc.name}\nPart: {acc.part_number or "N/A"}\nStock: {available}\nMin: {acc.min_qty}'
            Notification.objects.create(
                user=None,
                subject=subject,
                message=body,
                level='warning',
                target_url=acc.get_absolute_url(),
            )
            send_notification(subject, body)
            count += 1

        for con in low_consumables:
            available = con.available
            if available >= con.min_qty:
                continue
            subject = f'Low Stock: {con.name} ({available}/{con.min_qty} remaining)'
            body = f'{con.manufacturer.name} {con.name}\nPart: {con.part_number or "N/A"}\nStock: {available}\nMin: {con.min_qty}'
            Notification.objects.create(
                user=None,
                subject=subject,
                message=body,
                level='warning',
                target_url=con.get_absolute_url(),
            )
            send_notification(subject, body)
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Sent {count} low stock alerts.'))
