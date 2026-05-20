import base64
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from organization.models import Tenant, TenantMembership, TenantRole, AssetHolder
from users.models import Token
from rest_framework import status

User = get_user_model()

class SCIMProvisioningTests(TestCase):
    def setUp(self):
        # Create Tenants
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other Corp", slug="other")

        # Create Users
        self.admin_user = User.objects.create_user(
            username="admin", email="admin@acme.com", password="adminpassword"
        )
        self.inactive_user = User.objects.create_user(
            username="inactive", email="inactive@acme.com", password="password123", is_active=False
        )
        self.no_membership_user = User.objects.create_user(
            username="nomember", email="nomember@acme.com", password="password123"
        )

        # Create Tenant Roles
        self.role_member = TenantRole.objects.create(
            tenant=self.tenant,
            name="Member",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        self.role_admin = TenantRole.objects.create(
            tenant=self.tenant,
            name="Admin",
            permissions=["assets.view_asset", "assets.add_asset", "extras.view_dashboard"]
        )

        # Create Tenant Memberships
        TenantMembership.objects.create(
            user=self.admin_user,
            tenant=self.tenant,
            role=self.role_admin
        )
        
        # Setup tokens
        self.valid_token = Token.objects.create(
            user=self.admin_user,
            expires=timezone.now() + timezone.timedelta(days=1)
        )
        self.expired_token = Token.objects.create(
            user=self.admin_user,
            expires=timezone.now() - timezone.timedelta(hours=1)
        )
        self.inactive_token = Token.objects.create(
            user=self.inactive_user,
            expires=timezone.now() + timezone.timedelta(days=1)
        )
        self.no_membership_token = Token.objects.create(
            user=self.no_membership_user,
            expires=timezone.now() + timezone.timedelta(days=1)
        )

        # Headers helpers
        self.auth_headers = {
            'HTTP_AUTHORIZATION': f'Bearer {self.valid_token.key}'
        }

    def test_authentication_scenarios(self):
        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})

        # 1. No authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Valid token authentication
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 3. Invalid token key
        response = self.client.get(url, HTTP_AUTHORIZATION='Bearer invalidkey')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 4. Expired token key
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.expired_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 5. Inactive user token
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.inactive_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 6. User without tenant membership
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.no_membership_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 7. Basic Authentication fallback success
        basic_credentials = base64.b64encode(b'admin:adminpassword').decode('utf-8')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Basic {basic_credentials}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 8. Basic Authentication fallback failure
        basic_credentials_fail = base64.b64encode(b'admin:wrongpassword').decode('utf-8')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Basic {basic_credentials_fail}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_service_provider_config(self):
        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig", data["schemas"])
        self.assertTrue(data["patch"]["supported"])

    def test_user_list_and_filtering(self):
        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})

        # List should include admin_user
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["totalResults"], 1)
        self.assertEqual(data["Resources"][0]["userName"], self.admin_user.username)

        # Create another user in tenant to test filters
        user2 = User.objects.create_user(username="user2", email="user2@acme.com")
        TenantMembership.objects.create(user=user2, tenant=self.tenant, role=self.role_member)

        # Total count is 2
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 2)

        # Test eq filter
        response = self.client.get(f"{url}?filter=userName eq \"user2\"", **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["totalResults"], 1)
        self.assertEqual(data["Resources"][0]["userName"], "user2")

        # Test filters co
        response = self.client.get(f"{url}?filter=userName co \"user\"", **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 1)

    def test_user_creation_and_assetholder_linking(self):
        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "newuser@example.com",
            "name": {
                "familyName": "Doe",
                "givenName": "John"
            },
            "emails": [
                {
                    "value": "newuser@example.com",
                    "primary": True
                }
            ],
            "active": True
        }

        # Success path
        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        res_data = response.json()
        self.assertEqual(res_data["userName"], "newuser@example.com")
        self.assertEqual(res_data["name"]["givenName"], "John")

        # Verify User and AssetHolder and TenantMembership
        user = User.objects.get(username="newuser@example.com")
        self.assertTrue(user.is_active)
        
        membership = TenantMembership.objects.get(user=user, tenant=self.tenant)
        self.assertEqual(membership.role.name, "Member")

        holder = AssetHolder.objects.get(user=user, tenant=self.tenant)
        self.assertEqual(holder.email, "newuser@example.com")
        self.assertEqual(holder.first_name, "John")
        self.assertEqual(holder.last_name, "Doe")

        # Conflict check in same tenant
        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_user_creation_linking_existing_assetholder(self):
        # Create unlinked AssetHolder
        unlinked_holder = AssetHolder.objects.create(
            first_name="Jane",
            last_name="Smith",
            upn="jane@acme.com",
            email="jane@acme.com",
            tenant=self.tenant
        )

        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "jane@acme.com",
            "name": {
                "familyName": "Smith",
                "givenName": "Jane"
            },
            "emails": [
                {
                    "value": "jane@acme.com",
                    "primary": True
                }
            ],
            "active": True
        }

        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(username="jane@acme.com")
        unlinked_holder.refresh_from_db()
        self.assertEqual(unlinked_holder.user, user)

    def test_user_detail_put_patch_delete(self):
        # Create user
        user = User.objects.create_user(username="testuser", email="test@acme.com")
        TenantMembership.objects.create(user=user, tenant=self.tenant, role=self.role_member)
        AssetHolder.objects.create(
            user=user, first_name="Test", last_name="User", upn="test@acme.com", email="test@acme.com", tenant=self.tenant
        )

        detail_url = reverse('api:scim:user-detail', kwargs={'tenant_slug': self.tenant.slug, 'pk': user.id})

        # 1. GET Details
        response = self.client.get(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["userName"], "testuser")

        # 2. PUT Update
        put_payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "testuser_updated",
            "name": {
                "familyName": "UpdatedLastName",
                "givenName": "UpdatedFirstName"
            },
            "emails": [{"value": "updated@acme.com", "primary": True}],
            "active": False
        }
        response = self.client.put(detail_url, data=put_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        user.refresh_from_db()
        self.assertEqual(user.username, "testuser_updated")
        self.assertEqual(user.email, "updated@acme.com")
        self.assertFalse(user.is_active)

        holder = AssetHolder.objects.get(user=user, tenant=self.tenant)
        self.assertEqual(holder.first_name, "UpdatedFirstName")
        self.assertEqual(holder.last_name, "UpdatedLastName")

        # 3. PATCH Update
        patch_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {
                    "op": "replace",
                    "path": "active",
                    "value": True
                }
            ]
        }
        response = self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

        # 4. DELETE details
        response = self.client.delete(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(User.objects.filter(id=user.id).exists())
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(TenantMembership.objects.filter(user=user, tenant=self.tenant).exists())

    def test_group_crud_and_syncing(self):
        list_url = reverse('api:scim:group-list', kwargs={'tenant_slug': self.tenant.slug})

        # 1. GET groups list
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["totalResults"], 2)

        # 2. POST create group with members
        member_user = User.objects.create_user(username="memberuser", email="member@example.com")
        
        post_payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Software Managers",
            "members": [
                {"value": str(member_user.id)}
            ]
        }
        response = self.client.post(list_url, data=post_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        res_data = response.json()
        group_id = res_data["id"]
        self.assertEqual(res_data["displayName"], "Software Managers")

        role = TenantRole.objects.get(id=group_id, tenant=self.tenant)
        self.assertEqual(role.name, "Software Managers")
        
        membership = TenantMembership.objects.get(user=member_user, tenant=self.tenant)
        self.assertEqual(membership.role, role)

        # 3. GET Group Details
        detail_url = reverse('api:scim:group-detail', kwargs={'tenant_slug': self.tenant.slug, 'pk': group_id})
        response = self.client.get(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["displayName"], "Software Managers")

        # 4. PUT Group Update
        new_member = User.objects.create_user(username="newmember", email="newmember@example.com")
        put_payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Software Managers Updated",
            "members": [
                {"value": str(new_member.id)}
            ]
        }
        response = self.client.put(detail_url, data=put_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        role.refresh_from_db()
        self.assertEqual(role.name, "Software Managers Updated")
        
        self.assertTrue(TenantMembership.objects.filter(user=new_member, tenant=self.tenant, role=role).exists())
        self.assertFalse(TenantMembership.objects.filter(user=member_user, tenant=self.tenant, role=role).exists())

        # 5. PATCH Group Update
        patch_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {
                    "op": "add",
                    "path": "members",
                    "value": [
                        {"value": str(member_user.id)}
                    ]
                }
            ]
        }
        response = self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(TenantMembership.objects.filter(user=new_member, tenant=self.tenant, role=role).exists())
        self.assertTrue(TenantMembership.objects.filter(user=member_user, tenant=self.tenant, role=role).exists())

        # 6. DELETE Group
        response = self.client.delete(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TenantRole.objects.filter(id=group_id).exists())
        self.assertFalse(TenantMembership.objects.filter(role=role).exists())

    def test_filter_parsing_bracketed_emails(self):
        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        user2 = User.objects.create_user(username="user2", email="user2@acme.com")
        TenantMembership.objects.create(user=user2, tenant=self.tenant, role=self.role_member)

        response = self.client.get(f"{url}?filter=emails[type eq \"work\"].value eq \"user2@acme.com\"", **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["totalResults"], 1)
        self.assertEqual(data["Resources"][0]["userName"], "user2")

    def test_auth_no_tenant_resolved(self):
        response = self.client.get('/api/tenants//scim/v2/Users', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_graceful_assetholder_creation_on_integrity_error(self):
        from unittest.mock import patch
        from django.db import IntegrityError
        
        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "mockeduser@example.com",
            "name": {
                "familyName": "Mock",
                "givenName": "User"
            },
            "emails": [
                {
                    "value": "mockeduser@example.com",
                    "primary": True
                }
            ],
            "active": True
        }

        with patch('organization.models.AssetHolder.objects.create', side_effect=IntegrityError("Mocked constraint violation")):
            response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertTrue(User.objects.filter(username="mockeduser@example.com").exists())
