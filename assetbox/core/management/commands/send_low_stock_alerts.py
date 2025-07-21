from django.core.management.base import BaseCommand
from django.db import models

from core.models import Notification, EmailSettings
from assets.models import Accessory, Consumable


class Command(BaseCommand):
    help = 'Send low stock alerts for accessories and consumables below minimum quantity thresholds.'

    def handle(self, *args, **options):
        email_config = EmailSettings.load()
        if not email_config or not email_config.enabled:
            self.stdout.write(self.style.WARNING('Email notifications are disabled. Skipping.'))
            return

        low_accessories = Accessory.objects.filter(min_qty__gt=0).exclude(qty__gte=models.F('min_qty'))
        low_consumables = Consumable.objects.filter(min_qty__gt=0).exclude(qty__gte=models.F('min_qty'))

        count = 0

        for acc in low_accessories:
            subject = f'Low Stock: {acc.name} ({acc.remaining_qty}/{acc.qty} remaining)'
            body = f'{acc.manufacturer.name} {acc.name}\nPart: {acc.part_number or "N/A"}\nStock: {acc.remaining_qty}/{acc.qty}\nMin: {acc.min_qty}'
            Notification.objects.create(user=None, subject=subject, message=body, level='warning')
            count += 1

        for con in low_consumables:
            subject = f'Low Stock: {con.name} ({con.remaining_qty}/{con.qty} remaining)'
            body = f'{con.manufacturer.name} {con.name}\nPart: {con.part_number or "N/A"}\nStock: {con.remaining_qty}/{con.qty}\nMin: {con.min_qty}'
            Notification.objects.create(user=None, subject=subject, message=body, level='warning')
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Sent {count} low stock alerts.'))
