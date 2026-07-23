import hashlib
import hmac
import io
import json
import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, override_settings

from assets.models import Manufacturer
from core.managers import set_current_membership, set_current_tenant
from core.models import EmailSettings
from extras.models import FileAttachment, WebhookEndpoint
from licenses.models import License
from organization.models import Membership, Tenant
from software.models import Software
from users.models import Token


class CaptureRecoveryEvidenceCommandTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media.name)
        media_override.enable()
        self.addCleanup(media_override.disable)

        self.tenant = Tenant._base_manager.create(
            name='Recovery tenant', slug='recovery-tenant',
        )
        self.addCleanup(set_current_tenant, None)
        self.addCleanup(set_current_membership, None)
        set_current_tenant(self.tenant)

        manufacturer = Manufacturer.objects.create(
            name='Recovery manufacturer', slug='recovery-manufacturer',
        )
        software = Software.objects.create(
            name='Recovery software', manufacturer=manufacturer,
        )
        self.license = License.objects.create(
            name='Recovery license',
            software=software,
            tenant=self.tenant,
            product_key='license-canary-secret',
        )
        self.email = EmailSettings.objects.create(
            smtp_password='smtp-canary-secret',
        )
        self.webhook = WebhookEndpoint.objects.create(
            name='Recovery webhook',
            url='https://example.com/recovery-canary',
            secret='webhook-canary-secret',
            enabled=False,
            tenant=self.tenant,
        )
        license_type = ContentType.objects.get_for_model(License)
        self.attachment = FileAttachment.objects.create(
            model=license_type,
            object_id=self.license.pk,
            name='recovery-canary.txt',
            file=SimpleUploadedFile(
                'recovery-canary.txt', b'recovery-media-canary',
            ),
        )
        user = get_user_model().objects.create_user(
            username='recovery-probe-user',
        )
        Membership.objects.create(user=user, tenant=self.tenant)
        token = Token.objects.create(user=user, tenant=self.tenant)
        self.api_token = token.key

    def test_emits_comparable_evidence_without_protected_plaintext(self):
        stdout = io.StringIO()
        probe_key = 'recovery-probe-key-with-at-least-32-bytes'
        revision = 'a' * 40

        # Management commands run without request-scoped tenant context.
        set_current_membership(None)
        set_current_tenant(None)
        with patch.dict(os.environ, {
            'ITAMBOX_RECOVERY_PROBE_KEY': probe_key,
            'ITAMBOX_RECOVERY_API_TOKEN': self.api_token,
        }):
            call_command(
                'capture_recovery_evidence',
                revision=revision,
                license_pk=str(self.license.pk),
                email_settings_pk=str(self.email.pk),
                webhook_pk=str(self.webhook.pk),
                attachment_pk=str(self.attachment.pk),
                stdout=stdout,
            )

        raw_output = stdout.getvalue()
        evidence = json.loads(raw_output)
        expected_hmacs = {
            label: hmac.new(
                probe_key.encode(),
                label.encode() + b'\x00' + plaintext.encode(),
                hashlib.sha256,
            ).hexdigest()
            for label, plaintext in {
                'license_product_key': 'license-canary-secret',
                'smtp_password': 'smtp-canary-secret',
                'webhook_secret': 'webhook-canary-secret',
            }.items()
        }

        self.assertEqual(evidence['schema_version'], 1)
        self.assertEqual(evidence['declared_revision'], revision)
        self.assertNotIn('revision', evidence)
        self.assertEqual(evidence['protected_value_hmacs'], expected_hmacs)
        self.assertEqual(evidence['ciphertext_at_rest'], {
            'license_product_key': True,
            'smtp_password': True,
            'webhook_secret': True,
        })
        self.assertTrue(evidence['api_token_verified'])
        self.assertEqual(
            evidence['media']['name_hmac_sha256'],
            hmac.new(
                probe_key.encode(),
                b'media_name\x00recovery-canary.txt',
                hashlib.sha256,
            ).hexdigest(),
        )
        self.assertNotIn('name', evidence['media'])
        self.assertEqual(evidence['media']['size_bytes'], 21)
        self.assertEqual(
            evidence['database']['postgresql_version_num'],
            connection.pg_version,
        )
        self.assertIn(
            ['core', '0001_initial'],
            evidence['database']['applied_migrations'],
        )
        self.assertEqual(
            evidence['counts']['attachments'],
            FileAttachment._base_manager.count(),
        )

        for protected_value in (
            'license-canary-secret',
            'smtp-canary-secret',
            'webhook-canary-secret',
            self.api_token,
            probe_key,
        ):
            self.assertNotIn(protected_value, raw_output)
