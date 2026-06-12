"""
Idempotent backfill: encrypt any plaintext WebhookEndpoint.secret values that
were stored before the enc$-prefix save-hook was added.  Rows already encrypted
(starting with 'enc$') are left untouched so re-running migrate is safe.
"""
import logging
from django.db import migrations

logger = logging.getLogger(__name__)


def encrypt_existing_secrets(apps, schema_editor):
    from core.crypto import encrypt_string
    WebhookEndpoint = apps.get_model('extras', 'WebhookEndpoint')
    updated = 0
    for ep in WebhookEndpoint.objects.exclude(secret='').filter(secret__isnull=False):
        if not ep.secret.startswith('enc$'):
            ep.secret = encrypt_string(ep.secret)
            ep.save(update_fields=['secret'])
            updated += 1
    if updated:
        logger.info("extras.0028: encrypted %d WebhookEndpoint secret(s)", updated)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0027_remove_labeltemplate_printer_settings'),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_secrets, reverse_code=noop),
    ]
