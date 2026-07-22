import io
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.core.management import call_command, CommandError
from django.contrib.auth import get_user_model

from core.management.commands.sync_tenant_ldap import Command as SyncTenantLDAPCommand
from core.models import Job, EmailSettings
from organization.models import Tenant
from licenses.models import License

User = get_user_model()

class ManagementCommandsTestCase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def test_purge_deleted_command(self):
        call_command('purge_deleted', days=30, dry_run=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Total objects that would be purged", self.stdout.getvalue())

    def test_rotate_encryption_keys_command(self):
        from assets.models import Manufacturer
        from software.models import Software
        mfr = Manufacturer.objects.create(name="Microsoft", slug="microsoft")
        software = Software.objects.create(name="Office 365", manufacturer=mfr)
        License.objects.create(name="Office 365", software=software, product_key="enc$abc")
        call_command('rotate_encryption_keys', dry_run=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Scanning for encrypted fields", self.stdout.getvalue())

    def test_run_jobs_command(self):
        # Create a pending job
        Job.objects.create(name="Script: my_script.py", status=Job.STATUS_PENDING)
        call_command('run_jobs', stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Job processing complete", self.stdout.getvalue())

    def test_seed_data_command(self):
        # Run seed data with --production option to verify minimal bootstrap execution paths.
        # --force is required because seed_data refuses to clear data when DEBUG is off
        # (the guard that prevents an accidental production wipe).
        call_command('seed_data', production=True, force=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Database seeding complete", self.stdout.getvalue())

    def test_seed_data_refuses_to_wipe_without_force_when_not_debug(self):
        # The destructive clear must be blocked outside DEBUG unless --force is passed.
        from django.test import override_settings
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command('seed_data', production=True, stdout=self.stdout, stderr=self.stderr)

    def test_sync_tenant_ldap_command_invalid(self):
        with self.assertRaises(CommandError):
            call_command('sync_tenant_ldap', tenant='non-existent-tenant')


class SyncTenantLDAPDependencyTest(SimpleTestCase):
    @override_settings(ITAMBOX_TENANT_LDAP_CONFIGS={
        'test': {
            'SERVER_URI': 'ldap://127.0.0.1',
            'BIND_DN': 'cn=bind,dc=example,dc=test',
            'BIND_PASSWORD': 'test',
            'USER_SEARCH_BASE': 'ou=users,dc=example,dc=test',
            'USER_SEARCH_FILTER': '(uid=%(user)s)',
        },
    })
    @patch(
        'core.management.commands.sync_tenant_ldap.django_auth_ldap_installed',
        False,
    )
    @patch('core.management.commands.sync_tenant_ldap.ldap.initialize')
    def test_sync_tenant_ldap_requires_locked_native_dependencies(self, mock_ldap_init):
        stdout = io.StringIO()
        command = SyncTenantLDAPCommand(stdout=stdout)

        with self.assertRaisesRegex(
            CommandError,
            'locked Linux/WSL or Docker environment',
        ):
            command._run_sync(SimpleNamespace(slug='test', name='Test'))

        self.assertNotIn('Connecting to LDAP server', stdout.getvalue())
        mock_ldap_init.assert_not_called()
