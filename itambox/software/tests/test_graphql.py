"""GraphQL is read-only for the software and licenses domains (#612); writes are REST-only."""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from assets.models import Manufacturer
from core.schema import schema
from core.tests.mixins import grant
from itambox.middleware import set_current_tenant
from organization.models import Role, Tenant
from software.models import Software


class SoftwareGraphQLMutationsRemovedTestCase(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(username="swuser", email="sw@example.com", password="pw")
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a-sw")
        role = Role.objects.create(
            tenant=self.tenant,
            name="SW Role",
            permissions=[
                "software.view_software",
                "software.add_software",
                "software.change_software",
                "software.delete_software",
            ],
        )
        grant(self.user, self.tenant, role)
        set_current_tenant(self.tenant)
        self.manufacturer = Manufacturer.objects.create(name="Acme", slug="acme-sw")
        self.software = Software.objects.create(name="Keep Me", manufacturer=self.manufacturer, tenant=self.tenant)
        self.factory = RequestFactory()

    def tearDown(self):
        super().tearDown()
        set_current_tenant(None)

    def _context(self, active_tenant):
        request = self.factory.post("/graphql")
        request.user = self.User.objects.get(pk=self.user.pk)
        request.active_tenant = active_tenant
        return request

    def test_software_and_license_mutation_documents_are_rejected(self):
        documents = (
            f'mutation {{ createSoftware(name: "New Tool", manufacturerId: {self.manufacturer.pk}) {{ software {{ id }} }} }}',
            f'mutation {{ updateSoftware(id: {self.software.pk}, name: "Hacked") {{ software {{ id }} }} }}',
            f"mutation {{ deleteSoftware(id: {self.software.pk}) {{ success }} }}",
            f'mutation {{ createLicense(name: "L", softwareId: {self.software.pk}, seats: 1) {{ license {{ id }} }} }}',
            "mutation { deleteLicense(id: 1) { success } }",
        )
        context = self._context(self.tenant)
        for document in documents:
            result = schema.execute(document, context_value=context)
            self.assertIsNotNone(result.errors, document)
        self.assertEqual(list(Software.objects.values_list("name", flat=True)), ["Keep Me"])
