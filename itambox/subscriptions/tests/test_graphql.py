from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase

from assets.models import Asset, StatusLabel, Supplier
from core.schema import schema
from core.tests.mixins import grant
from itambox.middleware import set_current_tenant
from organization.models import AssetHolder, Role, Tenant, TenantGroup
from subscriptions.models import Subscription, SubscriptionAssignment


class SubscriptionsGraphQLTestCase(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(username="testuser", email="test@example.com", password="password")
        self.superuser = self.User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )

        self.tenant_group = TenantGroup.objects.create(name="Test Group", slug="test-group")
        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant", group=self.tenant_group)
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")

        # Create AssetHolder profile for self.user to link them to self.tenant
        self.holder = AssetHolder.objects.create(
            user=self.user,
            first_name="Test",
            last_name="User",
            upn="test.user",
            email="test@example.com",
            tenant=self.tenant,
        )

        # Grant permissions via Role + Membership (RBAC backend requires this)
        role = Role.objects.create(
            tenant=self.tenant,
            name="Test Role",
            permissions=[
                "subscriptions.view_subscription",
                "subscriptions.add_subscription",
                "subscriptions.change_subscription",
                "subscriptions.delete_subscription",
                "subscriptions.view_subscriptionassignment",
                "subscriptions.add_subscriptionassignment",
                "subscriptions.change_subscriptionassignment",
                "subscriptions.delete_subscriptionassignment",
                "assets.view_asset",
                "assets.view_supplier",
                "assets.add_asset",
            ],
        )
        grant(self.user, self.tenant, role)

        # Set thread-local tenant context for models creation
        set_current_tenant(self.tenant)

        self.supplier = Supplier.objects.create(name="Adobe", tenant=self.tenant)

        self.subscription = Subscription.objects.create(
            name="Creative Cloud", supplier=self.supplier, tenant=self.tenant
        )

        # Status label needed for Asset
        self.status_label = StatusLabel.objects.create(name="Active", slug="active", type="deployable")
        # Create an asset to assign the subscription to
        self.asset = Asset.objects.create(
            name="Test Asset", asset_tag="TAG-123", status=self.status_label, tenant=self.tenant
        )

        self.content_type = ContentType.objects.get_for_model(Asset)
        self.assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=self.content_type,
            object_id=self.asset.id,
            assigned_by=self.user,
        )

        self.factory = RequestFactory()

    def tearDown(self):
        super().tearDown()
        set_current_tenant(None)

    def get_context(self, user, tenant):
        request = self.factory.post("/graphql")
        request.user = get_user_model().objects.get(pk=user.pk)
        request.active_tenant = tenant
        return request

    def test_query_subscriptions(self):
        query = """
        query {
            subscriptions {
                id
                name
                supplier {
                    name
                }
            }
        }
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(query, context_value=context)

        self.assertIsNone(result.errors)
        subscriptions_data = result.data["subscriptions"]
        self.assertEqual(len(subscriptions_data), 1)
        self.assertEqual(subscriptions_data[0]["name"], "Creative Cloud")
        self.assertEqual(subscriptions_data[0]["supplier"]["name"], "Adobe")

    def test_query_subscription_assignments(self):
        query = """
        query {
            subscriptionAssignments {
                id
                subscription {
                    name
                }
                contentType {
                    model
                }
                objectId
            }
        }
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(query, context_value=context)

        self.assertIsNone(result.errors)
        assignments_data = result.data["subscriptionAssignments"]
        self.assertEqual(len(assignments_data), 1)
        self.assertEqual(int(assignments_data[0]["objectId"]), self.asset.id)

    def test_mutation_documents_are_rejected_for_every_removed_write(self):
        """#612: GraphQL is read-only; writes for these domains are REST-only."""
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        documents = (
            'mutation { createSubscription(name: "X") { subscription { id } } }',
            f'mutation {{ suspendSubscription(id: {self.subscription.pk}) {{ subscription {{ id }} }} }}',
            f'mutation {{ deleteSubscriptionAssignment(id: {self.assignment.pk}) {{ success }} }}',
            'mutation { createAccessory(name: "X") { accessory { id } } }',
            'mutation { createLicense(name: "X") { license { id } } }',
            'mutation { createSoftware(name: "X") { software { id } } }',
        )
        for document in documents:
            result = schema.execute(document, context_value=context)
            self.assertIsNotNone(result.errors, document)
        self.assertTrue(Subscription.objects.filter(pk=self.subscription.pk).exists())
        self.assertTrue(SubscriptionAssignment.objects.filter(pk=self.assignment.pk).exists())
