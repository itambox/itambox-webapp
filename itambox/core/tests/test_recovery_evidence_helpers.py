import hashlib
import hmac
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.management.commands import capture_recovery_evidence as command_module
from core.management.commands.capture_recovery_evidence import (
    build_recovery_evidence,
    evidence_hmac,
    validate_probe_context,
    validate_revision,
)


class RecoveryEvidenceHelperTests(SimpleTestCase):
    @staticmethod
    def _probe_objects(*, token_tenant_id=7, webhook_tenant_id=7,
                       token_expired=False):
        tenant = SimpleNamespace(pk=token_tenant_id, deleted_at=None)
        user = SimpleNamespace(is_active=True, is_superuser=True)
        return (
            SimpleNamespace(tenant_id=7),
            SimpleNamespace(tenant_id=webhook_tenant_id),
            SimpleNamespace(
                content_object=SimpleNamespace(tenant_id=7),
            ),
            SimpleNamespace(
                tenant_id=token_tenant_id,
                tenant=tenant,
                user=user,
                is_expired=token_expired,
            ),
        )

    def test_probe_context_rejects_expired_token(self):
        objects = self._probe_objects(token_expired=True)

        with self.assertRaisesMessage(ValidationError, 'expired'):
            validate_probe_context(*objects)

    def test_probe_context_rejects_cross_tenant_canaries(self):
        objects = self._probe_objects(webhook_tenant_id=8)

        with self.assertRaisesMessage(ValidationError, 'same tenant'):
            validate_probe_context(*objects)

    def test_probe_context_rejects_token_without_tenant(self):
        objects = list(self._probe_objects(token_tenant_id=None))
        objects[-1].tenant = None

        with self.assertRaisesMessage(ValidationError, 'tenant is required'):
            validate_probe_context(*objects)

    def test_hmac_is_domain_separated(self):
        key = b'k' * 32

        license_digest = evidence_hmac(key, 'license_product_key', b'same')
        webhook_digest = evidence_hmac(key, 'webhook_secret', b'same')

        self.assertNotEqual(license_digest, webhook_digest)
        self.assertEqual(
            license_digest,
            hmac.new(
                key,
                b'license_product_key\x00same',
                hashlib.sha256,
            ).hexdigest(),
        )

    def test_revision_must_be_full_lowercase_git_sha(self):
        self.assertEqual(validate_revision('a' * 40), 'a' * 40)
        for invalid in ('abc123', 'A' * 40, 'g' * 40):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    validate_revision(invalid)

    def test_builder_emits_only_comparable_non_plaintext_evidence(self):
        key = b'k' * 32
        protected_values = {
            'license_product_key': ('enc$license-ciphertext', 'license-secret'),
            'smtp_password': ('enc$smtp-ciphertext', 'smtp-secret'),
            'webhook_secret': ('enc$webhook-ciphertext', 'webhook-secret'),
        }

        evidence = build_recovery_evidence(
            revision='a' * 40,
            probe_key=key,
            protected_values=protected_values,
            api_token_verified=True,
            media_name='canary.txt',
            media_content=b'media-canary',
            counts={'attachments': 1, 'licenses': 1},
            postgresql_version_num=160014,
            applied_migrations=[('core', '0001_initial')],
        )

        rendered = json.dumps(evidence)
        self.assertEqual(evidence['schema_version'], 1)
        self.assertEqual(evidence['declared_revision'], 'a' * 40)
        self.assertNotIn('revision', evidence)
        self.assertEqual(evidence['ciphertext_at_rest'], {
            'license_product_key': True,
            'smtp_password': True,
            'webhook_secret': True,
        })
        self.assertEqual(
            evidence['media']['hmac_sha256'],
            evidence_hmac(key, 'media_content', b'media-canary'),
        )
        self.assertEqual(evidence['media']['size_bytes'], 12)
        self.assertEqual(
            evidence['media']['name_hmac_sha256'],
            evidence_hmac(key, 'media_name', b'canary.txt'),
        )
        self.assertNotIn('name', evidence['media'])
        self.assertEqual(evidence['database'], {
            'applied_migrations': [['core', '0001_initial']],
            'postgresql_version_num': 160014,
        })
        for plaintext in (
            'license-secret', 'smtp-secret', 'webhook-secret',
            'media-canary', 'canary.txt',
        ):
            self.assertNotIn(plaintext, rendered)

    def test_builder_rejects_unencrypted_or_empty_canaries(self):
        base = {
            'revision': 'a' * 40,
            'probe_key': b'k' * 32,
            'api_token_verified': True,
            'media_name': 'canary.txt',
            'media_content': b'media-canary',
            'counts': {},
            'postgresql_version_num': 160014,
            'applied_migrations': [],
        }
        for protected_values in (
            {'license_product_key': ('plaintext', 'secret')},
            {'license_product_key': ('enc$ciphertext', '')},
        ):
            with self.subTest(protected_values=protected_values):
                with self.assertRaises(ValidationError):
                    build_recovery_evidence(
                        protected_values=protected_values,
                        **base,
                    )

    def test_command_reads_explicit_canaries_and_emits_json(self):
        stdout = io.StringIO()
        attachment_file = MagicMock()
        attachment_file.read.return_value = b'media-canary'
        attachment = SimpleNamespace(
            name='canary.txt',
            file=attachment_file,
            content_object=SimpleNamespace(tenant_id=7),
        )

        model_names = (
            'License', 'EmailSettings', 'WebhookEndpoint',
            'FileAttachment', 'Token', 'MigrationRecorder', 'connection',
        )
        patches = {
            name: patch.object(command_module, name, create=True)
            for name in model_names
        }
        with (
            patches['License'] as license_model,
            patches['EmailSettings'] as email_model,
            patches['WebhookEndpoint'] as webhook_model,
            patches['FileAttachment'] as attachment_model,
            patches['Token'] as token_model,
            patches['MigrationRecorder'] as migration_recorder,
            patches['connection'] as connection,
            patch.dict(os.environ, {
                'ITAMBOX_RECOVERY_PROBE_KEY': 'k' * 32,
                'ITAMBOX_RECOVERY_API_TOKEN': 'api-token-canary',
            }),
        ):
            license_model._base_manager.get.return_value = SimpleNamespace(
                product_key='enc$license',
                decrypted_product_key='license-secret',
                tenant_id=7,
            )
            email_model._base_manager.get.return_value = SimpleNamespace(
                smtp_password='enc$smtp',
                smtp_password_decrypted='smtp-secret',
            )
            webhook_model._base_manager.get.return_value = SimpleNamespace(
                secret='enc$webhook',
                secret_decrypted='webhook-secret',
                tenant_id=7,
            )
            attachment_model._base_manager.get.return_value = attachment
            for model in (
                license_model, email_model, webhook_model, attachment_model,
            ):
                model._base_manager.count.return_value = 1
            token_model.find_by_key.return_value = SimpleNamespace(
                is_expired=False,
                tenant_id=7,
                tenant=SimpleNamespace(deleted_at=None),
                user=SimpleNamespace(is_active=True, is_superuser=True),
            )
            connection.vendor = 'postgresql'
            connection.pg_version = 160014
            migration_recorder.return_value.applied_migrations.return_value = {
                ('core', '0001_initial'),
            }

            call_command(
                'capture_recovery_evidence',
                revision='a' * 40,
                license_pk='11',
                email_settings_pk='12',
                webhook_pk='13',
                attachment_pk='14',
                stdout=stdout,
            )

        evidence = json.loads(stdout.getvalue())
        self.assertTrue(evidence['api_token_verified'])
        self.assertEqual(
            evidence['media']['name_hmac_sha256'],
            evidence_hmac(b'k' * 32, 'media_name', b'canary.txt'),
        )
        self.assertNotIn('name', evidence['media'])
        self.assertEqual(evidence['counts'], {
            'attachments': 1,
            'email_settings': 1,
            'licenses': 1,
            'webhooks': 1,
        })
        self.assertEqual(evidence['database'], {
            'applied_migrations': [['core', '0001_initial']],
            'postgresql_version_num': 160014,
        })
        license_model._base_manager.get.assert_called_once_with(pk='11')
        attachment_file.open.assert_called_once_with('rb')
        attachment_file.close.assert_called_once_with()

    @patch.object(command_module, 'connection')
    def test_command_rejects_short_probe_key_before_database_queries(
        self, connection,
    ):
        connection.vendor = 'postgresql'
        with patch.dict(os.environ, {
            'ITAMBOX_RECOVERY_PROBE_KEY': 'too-short',
            'ITAMBOX_RECOVERY_API_TOKEN': 'api-token-canary',
        }, clear=True):
            with self.assertRaisesMessage(
                CommandError,
                'ITAMBOX_RECOVERY_PROBE_KEY must contain at least 32 bytes',
            ):
                call_command(
                    'capture_recovery_evidence',
                    revision='a' * 40,
                    license_pk='11',
                    email_settings_pk='12',
                    webhook_pk='13',
                    attachment_pk='14',
                )

    def test_command_sanitizes_unreadable_media_error(self):
        model_names = (
            'License', 'EmailSettings', 'WebhookEndpoint',
            'FileAttachment', 'Token', 'connection',
        )
        patches = {
            name: patch.object(command_module, name, create=True)
            for name in model_names
        }
        with (
            patches['License'] as license_model,
            patches['EmailSettings'] as email_model,
            patches['WebhookEndpoint'] as webhook_model,
            patches['FileAttachment'] as attachment_model,
            patches['Token'] as token_model,
            patches['connection'] as connection,
            patch.dict(os.environ, {
                'ITAMBOX_RECOVERY_PROBE_KEY': 'k' * 32,
                'ITAMBOX_RECOVERY_API_TOKEN': 'api-token-canary',
            }),
        ):
            connection.vendor = 'postgresql'
            license_model._base_manager.get.return_value = SimpleNamespace(
                tenant_id=7,
            )
            email_model._base_manager.get.return_value = object()
            webhook_model._base_manager.get.return_value = SimpleNamespace(
                tenant_id=7,
            )
            attachment = attachment_model._base_manager.get.return_value
            attachment.content_object = SimpleNamespace(tenant_id=7)
            attachment.file.open.side_effect = FileNotFoundError(
                '/private/storage/canary.txt',
            )
            token_model.find_by_key.return_value = SimpleNamespace(
                is_expired=False,
                tenant_id=7,
                tenant=SimpleNamespace(deleted_at=None),
                user=SimpleNamespace(is_active=True, is_superuser=True),
            )

            with self.assertRaisesMessage(
                CommandError,
                'Recovery attachment could not be read',
            ) as raised:
                call_command(
                    'capture_recovery_evidence',
                    revision='a' * 40,
                    license_pk='11',
                    email_settings_pk='12',
                    webhook_pk='13',
                    attachment_pk='14',
                )

        self.assertNotIn('/private/storage', str(raised.exception))
