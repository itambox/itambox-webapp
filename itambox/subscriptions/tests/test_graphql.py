from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from graphql import GraphQLError

from core.schema import schema
from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from organization.models import Tenant, TenantGroup, AssetHolder
from assets.models import Supplier, Asset, StatusLabel
from itambox.middleware import set_current_tenant

class SubscriptionsGraphQLTestCase(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(username='testuser', email='test@example.com', password='password')
        self.superuser = self.User.objects.create_superuser(username='admin', email='admin@example.com', password='password')
        
        self.tenant_group = TenantGroup.objects.create(name='Test Group', slug='test-group')
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant', group=self.tenant_group)
        self.other_tenant = Tenant.objects.create(name='Other Tenant', slug='other-tenant')

        # Add all permissions
        from django.contrib.auth.models import Permission
        permissions = Permission.objects.filter(codename__in=[
            'view_provider', 'add_provider', 'change_provider', 'delete_provider',
            'view_subscription', 'add_subscription', 'change_subscription', 'delete_subscription',
            'view_subscriptionassignment', 'add_subscriptionassignment', 'change_subscriptionassignment', 'delete_subscriptionassignment',
            'view_asset', 'add_asset'
        ])
        self.user.user_permissions.add(*permissions)

        # Create AssetHolder profile for self.user to link them to self.tenant for custom RBAC backend evaluation
        self.holder = AssetHolder.objects.create(
            user=self.user,
            first_name="Test",
            last_name="User",
            upn="test.user",
            email="test@example.com",
            tenant=self.tenant
        )

        # Set thread-local tenant context for models creation
        set_current_tenant(self.tenant)

        self.provider = Provider.objects.create(name='Adobe', tenant=self.tenant)
        self.group_provider = Provider.objects.create(name='Group Corp', tenant_group=self.tenant_group)
        self.global_provider = Provider.objects.create(name='Global Corp', tenant=None)

        self.subscription = Subscription.objects.create(
            name='Creative Cloud',
            provider=self.provider,
            tenant=self.tenant
        )

        # Status label needed for Asset
        self.status_label = StatusLabel.objects.create(name='Active', slug='active', type='deployable')
        # Create an asset to assign the subscription to
        self.asset = Asset.objects.create(
            name='Test Asset',
            asset_tag='TAG-123',
            status=self.status_label,
            tenant=self.tenant
        )

        self.content_type = ContentType.objects.get_for_model(Asset)
        self.assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=self.content_type,
            object_id=self.asset.id,
            assigned_by=self.user
        )

        self.factory = RequestFactory()

    def tearDown(self):
        super().tearDown()
        set_current_tenant(None)

    def get_context(self, user, tenant):
        request = self.factory.post('/graphql')
        request.user = get_user_model().objects.get(pk=user.pk)
        request.active_tenant = tenant
        return request

    def test_query_providers(self):
        query = """
        query {
            providers {
                id
                name
                tenant {
                    id
                }
                tenantGroup {
                    id
                }
            }
        }
        """
        # Execute in context of tenant
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(query, context_value=context)
        
        self.assertIsNone(result.errors)
        providers_data = result.data['providers']
        
        # Should return the tenant-scoped provider, the group-scoped provider, and the global provider
        names = {p['name'] for p in providers_data}
        self.assertIn('Adobe', names)
        self.assertIn('Group Corp', names)
        self.assertIn('Global Corp', names)

    def test_query_subscriptions(self):
        query = """
        query {
            subscriptions {
                id
                name
                provider {
                    name
                }
            }
        }
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(query, context_value=context)

        self.assertIsNone(result.errors)
        subscriptions_data = result.data['subscriptions']
        self.assertEqual(len(subscriptions_data), 1)
        self.assertEqual(subscriptions_data[0]['name'], 'Creative Cloud')

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
        assignments_data = result.data['subscriptionAssignments']
        self.assertEqual(len(assignments_data), 1)
        self.assertEqual(int(assignments_data[0]['objectId']), self.asset.id)

    def test_create_provider_tenant_scoped(self):
        mutation = """
        mutation {
            createProvider(name: "Microsoft", isActive: true) {
                provider {
                    name
                    tenant {
                        id
                    }
                    tenantGroup {
                        id
                    }
                }
            }
        }
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        provider_data = result.data['createProvider']['provider']
        self.assertEqual(provider_data['name'], 'Microsoft')
        self.assertEqual(int(provider_data['tenant']['id']), self.tenant.id)
        self.assertIsNone(provider_data['tenantGroup'])

    def test_create_provider_tenant_group_scoped(self):
        tenant_group = TenantGroup.objects.create(name="Regional Group", slug="regional-group")
        mutation = f"""
        mutation {{
            createProvider(name: "Regional Provider", tenantGroupId: {tenant_group.id}) {{
                provider {{
                    name
                    tenantGroup {{
                        id
                    }}
                    tenant {{
                        id
                    }}
                }}
            }}
        }}
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        provider_data = result.data['createProvider']['provider']
        self.assertEqual(provider_data['name'], 'Regional Provider')
        self.assertEqual(int(provider_data['tenantGroup']['id']), tenant_group.id)
        self.assertIsNone(provider_data['tenant'])

    def test_create_provider_global_denied_for_standard_user(self):
        mutation = """
        mutation {
            createProvider(name: "Global Microsoft", tenantId: null, tenantGroupId: null) {
                provider {
                    name
                }
            }
        }
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNotNone(result.errors)
        self.assertIn("Only superusers can create global providers.", result.errors[0].message)

    def test_create_provider_global_allowed_for_superuser(self):
        mutation = """
        mutation {
            createProvider(name: "Global Microsoft", tenantId: null, tenantGroupId: null) {
                provider {
                    name
                    tenant {
                        id
                    }
                    tenantGroup {
                        id
                    }
                }
            }
        }
        """
        set_current_tenant(self.tenant)
        context = self.get_context(self.superuser, self.tenant)
        result = schema.execute(mutation, context_value=context)

        self.assertIsNone(result.errors)
        provider_data = result.data['createProvider']['provider']
        self.assertEqual(provider_data['name'], 'Global Microsoft')
        self.assertIsNone(provider_data['tenant'])
        self.assertIsNone(provider_data['tenantGroup'])

    def test_create_subscription(self):
        mutation = f"""
        mutation {{
            createSubscription(name: "Office 365", providerId: {self.provider.id}, type: "saas", status: "active") {{
                subscription {{
                    name
                    provider {{
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
        sub_data = result.data['createSubscription']['subscription']
        self.assertEqual(sub_data['name'], 'Office 365')
        self.assertEqual(sub_data['provider']['name'], 'Adobe')

    def test_create_subscription_assignment(self):
        # Create another asset to assign
        set_current_tenant(self.tenant)
        other_asset = Asset.objects.create(
            name='Other Asset',
            asset_tag='TAG-456',
            status=self.status_label,
            tenant=self.tenant
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
        assignment_data = result.data['createSubscriptionAssignment']['subscriptionAssignment']
        self.assertEqual(assignment_data['notes'], 'Assigned to test node')

    def test_query_provider_contacts(self):
        from organization.models import Contact, ContactRole, ContactAssignment
        # Fetch or create the role
        role, _ = ContactRole.objects.get_or_create(
            slug="primary-contact",
            defaults={"name": "Primary Contact", "description": "Primary Contact"}
        )
        # Create a contact
        contact = Contact.objects.create(
            name="Adobe Primary Agent",
            phone="123456",
            email="adobe-agent@example.com",
            web_url="https://adobe.example.com"
        )
        # Assign contact to provider
        ContactAssignment.objects.create(
            contact=contact,
            role=role,
            content_type=ContentType.objects.get_for_model(Provider),
            object_id=self.provider.id,
            priority="primary"
        )

        query = """
        query {
            provider(id: "%s") {
                contacts {
                    priority
                    contact {
                        name
                        phone
                        email
                        webUrl
                    }
                    role {
                        name
                        slug
                    }
                }
            }
        }
        """ % self.provider.id

        set_current_tenant(self.tenant)
        context = self.get_context(self.user, self.tenant)
        result = schema.execute(query, context_value=context)

        self.assertIsNone(result.errors)
        contacts_data = result.data['provider']['contacts']
        self.assertEqual(len(contacts_data), 1)
        self.assertEqual(contacts_data[0]['priority'], 'PRIMARY')
        self.assertEqual(contacts_data[0]['contact']['name'], 'Adobe Primary Agent')
        self.assertEqual(contacts_data[0]['contact']['phone'], '123456')
        self.assertEqual(contacts_data[0]['contact']['email'], 'adobe-agent@example.com')
        self.assertEqual(contacts_data[0]['contact']['webUrl'], 'https://adobe.example.com')
        self.assertEqual(contacts_data[0]['role']['slug'], 'primary-contact')
