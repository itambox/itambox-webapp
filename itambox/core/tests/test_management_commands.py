import io
from unittest.mock import patch, MagicMock
from django.test import TransactionTestCase
from django.core.management import call_command, CommandError
from django.contrib.auth import get_user_model

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
        # Run seed data with --production option to verify minimal bootstrap execution paths
        call_command('seed_data', production=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Database seeding complete", self.stdout.getvalue())

    def test_sync_tenant_ldap_command_invalid(self):
        with self.assertRaises(CommandError):
            call_command('sync_tenant_ldap', tenant='non-existent-tenant')
