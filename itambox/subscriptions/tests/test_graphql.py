from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from graphql import GraphQLError

from assets.models import Asset, StatusLabel, Supplier
from core.schema import schema
from core.tests.mixins import grant
from itambox.middleware import set_current_tenant
from organization.models import AssetHolder, Role, Tenant, TenantGroup
from subscriptions.models import Subscription, SubscriptionAssignment, SubscriptionStatusChoices


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

    def test_create_subscription(self):
        mutation = f"""
        mutation {{
            createSubscription(name: "Office 365", supplierId: {self.supplier.id}, type: "saas", status: "active") {{
                subscription {{
                    name
                    supplier {{
                        name
                    }}
                }}
            }}
        }}
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        sub_data = result.data["createSubscription"]["subscription"]
        self.assertEqual(sub_data["name"], "Office 365")
        self.assertEqual(sub_data["supplier"]["name"], "Adobe")

    def test_create_subscription_with_global_supplier(self):
        supplier = Supplier.objects.create(name="Globex", slug="globex")

        mutation = f"""
        mutation {{
            createSubscription(name: "Figma", supplierId: {supplier.id}, type: "saas", status: "active") {{
                subscription {{
                    name
                    supplier {{
                        name
                    }}
                }}
            }}
        }}
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        sub_data = result.data["createSubscription"]["subscription"]
        self.assertEqual(sub_data["supplier"]["name"], "Globex")

    def test_create_subscription_with_group_scoped_supplier(self):
        supplier = Supplier.objects.create(name="Group Vendor", slug="group-vendor", tenant_group=self.tenant_group)

        mutation = f"""
        mutation {{
            createSubscription(name: "Canva", supplierId: {supplier.id}, type: "saas", status: "active") {{
                subscription {{
                    name
                    supplier {{
                        name
                    }}
                }}
            }}
        }}
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        sub_data = result.data["createSubscription"]["subscription"]
        self.assertEqual(sub_data["supplier"]["name"], "Group Vendor")

    def test_create_subscription_rejects_supplier_from_foreign_group(self):
        other_group = TenantGroup.objects.create(name="Other Group", slug="other-group")
        supplier = Supplier.objects.create(name="Foreign Vendor", slug="foreign-vendor", tenant_group=other_group)

        mutation = f"""
        mutation {{
            createSubscription(name: "Notion", supplierId: {supplier.id}, type: "saas", status: "active") {{
                subscription {{
                    name
                }}
            }}
        }}
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNotNone(result.errors)
        self.assertIn("Permission denied", str(result.errors[0]))

    def test_update_subscription_accepts_a_global_supplier(self):
        supplier = Supplier.objects.create(name="Globex", slug="globex")

        mutation = f"""
        mutation {{
            updateSubscription(id: {self.subscription.id}, supplierId: {supplier.id}) {{
                subscription {{
                    supplier {{
                        name
                    }}
                }}
            }}
        }}
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        sub_data = result.data["updateSubscription"]["subscription"]
        self.assertEqual(sub_data["supplier"]["name"], "Globex")

    def test_explicit_lifecycle_mutations_suspend_and_resume(self):
        context = self.get_context(self.user, self.tenant)
        set_current_tenant(self.tenant)

        result = schema.execute(
            f"""
            mutation {{
                updateSubscription(id: {self.subscription.id}, status: "suspended") {{
                    subscription {{ status }}
                }}
            }}
            """,
            context_value=context,
        )
        self.assertIsNotNone(result.errors)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, SubscriptionStatusChoices.ACTIVE)

        result = schema.execute(
            f"""
            mutation {{
                suspendSubscription(id: {self.subscription.id}) {{
                    subscription {{ status }}
                }}
            }}
            """,
            context_value=context,
        )
        self.assertIsNone(result.errors)
        self.assertEqual(result.data["suspendSubscription"]["subscription"]["status"], "SUSPENDED")

        result = schema.execute(
            f"""
            mutation {{
                resumeSubscription(id: {self.subscription.id}) {{
                    subscription {{ status }}
                }}
            }}
            """,
            context_value=context,
        )
        self.assertIsNone(result.errors)
        self.assertEqual(result.data["resumeSubscription"]["subscription"]["status"], "ACTIVE")

        result = schema.execute(
            f"""
            mutation {{
                renewSubscription(id: {self.subscription.id}, renewalDate: "2031-02-03") {{
                    subscription {{ status renewalDate }}
                }}
            }}
            """,
            context_value=context,
        )
        self.assertIsNone(result.errors)
        self.assertEqual(result.data["renewSubscription"]["subscription"]["renewalDate"], "2031-02-03")

        result = schema.execute(
            f"""
            mutation {{
                cancelSubscription(id: {self.subscription.id}, reason: "No longer needed") {{
                    subscription {{ status }}
                }}
            }}
            """,
            context_value=context,
        )
        self.assertIsNone(result.errors)
        self.assertEqual(result.data["cancelSubscription"]["subscription"]["status"], "CANCELLED")

    def test_create_subscription_assignment(self):
        # Create another asset to assign
        set_current_tenant(self.tenant)
        other_asset = Asset.objects.create(
            name="Other Asset", asset_tag="TAG-456", status=self.status_label, tenant=self.tenant
        )
        mutation = f"""
        mutation {{
            createSubscriptionAssignment(
                subscriptionId: {self.subscription.id},
                contentTypeId: {self.content_type.id},
                objectId: {other_asset.id},
                notes: "Assigned to test node"
            ) {{
                subscriptionAssignment {{
                    id
                    notes
                }}
            }}
        }}
        """
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        assignment_data = result.data["createSubscriptionAssignment"]["subscriptionAssignment"]
        self.assertEqual(assignment_data["notes"], "Assigned to test node")
