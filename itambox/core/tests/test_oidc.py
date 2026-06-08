from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from core.managers import set_current_tenant, get_current_tenant
from core.auth.oidc import TenantOIDCBackend, TenantOIDCAuthorizeView, TenantOIDCCallbackView
from organization.models import Tenant, TenantMembership, TenantRole, AssetHolder

User = get_user_model()

TEST_OIDC_CONFIGS = {
    "tenant-alpha": {
        "OIDC_RP_CLIENT_ID": "alpha-client-id",
        "OIDC_RP_CLIENT_SECRET": "alpha-secret",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://auth.alpha.com/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://auth.alpha.com/token",
        "OIDC_OP_USER_ENDPOINT": "https://auth.alpha.com/userinfo",
        "OIDC_GROUP_ROLE_MAPPING": {
            "alpha-admins": "Admin",
            "alpha-managers": "Manager",
            "alpha-members": "Member"
        }
    },
    "tenant-beta": {
        "OIDC_RP_CLIENT_ID": "beta-client-id",
        "OIDC_RP_CLIENT_SECRET": "beta-secret",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://auth.beta.org/oauth2/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://auth.beta.org/oauth2/token",
        "OIDC_OP_USER_ENDPOINT": "https://auth.beta.org/oauth2/userinfo",
        "OIDC_GROUP_ROLE_MAPPING": {
            "beta-staff": "Manager",
            "beta-users": "Member"
        }
    }
}

@override_settings(
    ITAMBOX_TENANT_OIDC_CONFIGS=TEST_OIDC_CONFIGS
)
class TenantOIDCTestCase(TestCase):
    def setUp(self):
        set_current_tenant(None)
        self.tenant_alpha = Tenant.objects.create(name="Alpha Tenant", slug="tenant-alpha")
        self.tenant_beta = Tenant.objects.create(name="Beta Tenant", slug="tenant-beta")

    def tearDown(self):
        set_current_tenant(None)

    def test_settings_routing(self):
        backend = TenantOIDCBackend()

        # No tenant context: fallback to default settings or raise
        set_current_tenant(None)
        self.assertEqual(backend.OIDC_RP_SIGN_ALGO, "RS256")

        # Alpha Tenant context
        set_current_tenant(self.tenant_alpha)
        self.assertEqual(backend.OIDC_RP_CLIENT_ID, "alpha-client-id")
        self.assertEqual(backend.OIDC_RP_CLIENT_SECRET, "alpha-secret")

        # Beta Tenant context
        set_current_tenant(self.tenant_beta)
        self.assertEqual(backend.OIDC_RP_CLIENT_ID, "beta-client-id")
        self.assertEqual(backend.OIDC_RP_CLIENT_SECRET, "beta-secret")

    def test_user_creation_and_update(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        claims = {
            "email": "user1@alpha.com",
            "sub": "sub-12345",
            "given_name": "Alice",
            "family_name": "Smith",
            "groups": ["alpha-members"]
        }

        # Create User
        user = backend.create_user(claims)
        self.assertEqual(user.email, "user1@alpha.com")
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Smith")

        # Unique suffix test: create another user with same base username
        user2 = backend.create_user(claims)
        self.assertNotEqual(user.username, user2.username)
        self.assertTrue(user2.username.startswith(user.username))

        # Update User
        update_claims = {
            "email": "user1-updated@alpha.com",
            "given_name": "Alice-Updated",
            "family_name": "Smith-Updated",
        }
        updated_user = backend.update_user(user, update_claims)
        self.assertEqual(updated_user.email, "user1-updated@alpha.com")
        self.assertEqual(updated_user.first_name, "Alice-Updated")
        self.assertEqual(updated_user.last_name, "Smith-Updated")

    def test_assetholder_provisioning_and_linking(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        # Scenario A: Pre-existing AssetHolder without a user
        existing_holder = AssetHolder.objects.create(
            first_name="Pre",
            last_name="Existing",
            upn="pre@alpha.com",
            email="pre@alpha.com",
            tenant=self.tenant_alpha
        )

        claims_a = {
            "email": "pre@alpha.com",
            "sub": "sub-a",
            "given_name": "Pre",
            "family_name": "Existing"
        }

        user_a = backend.create_user(claims_a)
        existing_holder.refresh_from_db()
        self.assertEqual(existing_holder.user, user_a)

        # Scenario B: No existing AssetHolder (auto-provisions a new one)
        claims_b = {
            "email": "new@alpha.com",
            "sub": "sub-b",
            "given_name": "New",
            "family_name": "User"
        }
        user_b = backend.create_user(claims_b)
        new_holder = AssetHolder.objects.get(user=user_b, tenant=self.tenant_alpha)
        self.assertEqual(new_holder.email, "new@alpha.com")
        self.assertEqual(new_holder.upn, "new@alpha.com")

    def test_group_to_role_synchronization(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        # Setup OIDC claims mapping to Admin
        claims_admin = {
            "email": "admin@alpha.com",
            "sub": "sub-admin",
            "groups": ["alpha-admins", "alpha-members"]
        }
        user_admin = backend.create_user(claims_admin)

        membership_admin = TenantMembership.objects.get(user=user_admin, tenant=self.tenant_alpha)
        self.assertEqual(membership_admin.role.name, "Admin")
        # Admin should have delete permission on normal models
        self.assertTrue(any("delete_" in p for p in membership_admin.role.permissions if "dashboard" not in p))

        # Setup OIDC claims mapping to Manager (lower priority than Admin, higher than Member)
        claims_manager = {
            "email": "manager@alpha.com",
            "sub": "sub-mgr",
            "groups": ["alpha-managers", "alpha-members"]
        }
        user_mgr = backend.create_user(claims_manager)

        membership_mgr = TenantMembership.objects.get(user=user_mgr, tenant=self.tenant_alpha)
        self.assertEqual(membership_mgr.role.name, "Manager")
        # Manager should not have delete permission on normal models but should have add/change
        self.assertFalse(any("delete_" in p for p in membership_mgr.role.permissions if "dashboard" not in p))

        # Fallback to Member
        claims_fallback = {
            "email": "member@alpha.com",
            "sub": "sub-mem",
            "groups": ["unknown-group"]
        }
        user_mem = backend.create_user(claims_fallback)

        membership_mem = TenantMembership.objects.get(user=user_mem, tenant=self.tenant_alpha)
        self.assertEqual(membership_mem.role.name, "Member")

    def test_authorize_view_tenant_routing(self):
        # Access with slug in kwargs
        url = reverse('oidc_authentication_init_tenant', kwargs={'tenant_slug': 'tenant-alpha'})
        response = self.client.get(url)
        self.assertEqual(self.client.session.get('oidc_tenant_slug'), 'tenant-alpha')
        self.assertEqual(response.status_code, 302)
        self.assertIn("https://auth.alpha.com/authorize", response.url)

        # Access with slug in query parameters
        url_query = reverse('oidc_authentication_init') + "?tenant=tenant-beta"
        response_query = self.client.get(url_query)
        self.assertEqual(self.client.session.get('oidc_tenant_slug'), 'tenant-beta')
        self.assertEqual(response_query.status_code, 302)
        self.assertIn("https://auth.beta.org/oauth2/authorize", response_query.url)

    @patch('core.auth.oidc.TenantOIDCCallbackView.get_settings')
    @patch('django.contrib.auth.authenticate')
    def test_callback_view_tenant_routing(self, mock_authenticate, mock_get_settings):
        # Configure state/nonce session parameters for Callback view safety checks
        session = self.client.session
        session['oidc_states'] = {
            'state-123': {
                'nonce': 'nonce-123',
                'code_verifier': 'verifier-123'
            }
        }
        session['oidc_tenant_slug'] = 'tenant-alpha'
        session.save()

        user = User.objects.create_user(username="test_callback_user", email="callback@test.com")
        user.backend = 'core.auth.oidc.TenantOIDCBackend'
        mock_authenticate.return_value = user

        mock_get_settings.side_effect = lambda attr, *args: "/" if attr == 'OIDC_REDIRECT_OK' else (args[0] if args else None)

        # Execute callback HTTP request
        url = reverse('oidc_authentication_callback') + "?code=code-123&state=state-123"
        response = self.client.get(url)

        # Verify active tenant session key and redirect status
        self.assertEqual(self.client.session.get('active_tenant_id'), self.tenant_alpha.pk)
        self.assertEqual(response.status_code, 302)

    @override_settings(
        ITAMBOX_TENANT_OIDC_CONFIGS={
            "tenant-alpha": {
                "OIDC_RP_CLIENT_ID": "alpha-client-id",
                "OIDC_RP_CLIENT_SECRET": "alpha-secret",
                "OIDC_GROUP_ROLE_MAPPING": {
                    "alpha-admins": "admin",      # lowercase mapping
                    "alpha-managers": "MANAGER",  # uppercase mapping
                    "alpha-members": "MeMbEr"    # mixed-case mapping
                }
            }
        }
    )
    def test_case_insensitive_group_role_mapping(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        claims_admin = {
            "email": "admin-ci@alpha.com",
            "sub": "sub-admin-ci",
            "groups": ["alpha-admins"]
        }
        user_admin = backend.create_user(claims_admin)
        membership_admin = TenantMembership.objects.get(user=user_admin, tenant=self.tenant_alpha)
        self.assertEqual(membership_admin.role.name, "Admin")

        claims_mgr = {
            "email": "mgr-ci@alpha.com",
            "sub": "sub-mgr-ci",
            "groups": ["alpha-managers"]
        }
        user_mgr = backend.create_user(claims_mgr)
        membership_mgr = TenantMembership.objects.get(user=user_mgr, tenant=self.tenant_alpha)
        self.assertEqual(membership_mgr.role.name, "Manager")

        claims_mem = {
            "email": "mem-ci@alpha.com",
            "sub": "sub-mem-ci",
            "groups": ["alpha-members"]
        }
        user_mem = backend.create_user(claims_mem)
        membership_mem = TenantMembership.objects.get(user=user_mem, tenant=self.tenant_alpha)
        self.assertEqual(membership_mem.role.name, "Member")

    def test_authorize_view_invalid_tenant_slug(self):
        # Access with an invalid slug in kwargs
        url = reverse('oidc_authentication_init_tenant', kwargs={'tenant_slug': 'non-existent-tenant'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

        # Access with an invalid slug in query parameters
        url_query = reverse('oidc_authentication_init') + "?tenant=non-existent-tenant"
        response_query = self.client.get(url_query)
        self.assertEqual(response_query.status_code, 404)

    def test_upn_collision_during_provisioning(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        # Create user A and link to an AssetHolder with specific upn
        user_a = User.objects.create_user(username="user_a", email="usera@alpha.com")
        holder_a = AssetHolder.objects.create(
            user=user_a,
            first_name="User",
            last_name="A",
            upn="collision-upn@alpha.com",
            email="usera@alpha.com",
            tenant=self.tenant_alpha
        )

        # Now login user B with claims that have the same upn
        claims_b = {
            "email": "userb@alpha.com",
            "sub": "sub-user-b",
            "upn": "collision-upn@alpha.com",  # collision!
            "given_name": "User",
            "family_name": "B"
        }

        # This should log warning and continue without raising IntegrityError/crashing
        with self.assertLogs('core.auth.oidc', level='WARNING') as cm:
            user_b = backend.create_user(claims_b)
            self.assertIsNotNone(user_b)
            # Check that warning was logged
            log_output = "".join(cm.output)
            self.assertIn("IntegrityError while creating AssetHolder", log_output)

        # Verify user_b was created but has no asset_holder_profile (or it wasn't duplicate created)
        user_b.refresh_from_db()
        self.assertFalse(user_b.asset_holder_profiles.filter(tenant=self.tenant_alpha).exists())

    def test_multitenant_user_login_different_tenant(self):
        backend = TenantOIDCBackend()
        
        # Log in first on tenant-alpha
        set_current_tenant(self.tenant_alpha)
        claims = {
            "email": "multi@test.com",
            "sub": "sub-multi",
            "given_name": "Multi",
            "family_name": "User",
            "groups": ["alpha-members"]
        }
        user = backend.create_user(claims)
        
        # Verify the user has a profile in tenant-alpha
        self.assertEqual(user.asset_holder_profiles.filter(tenant=self.tenant_alpha).first().tenant, self.tenant_alpha)
        
        # Now log in on tenant-beta
        set_current_tenant(self.tenant_beta)
        beta_claims = {
            "email": "multi@test.com",
            "sub": "sub-multi",
            "given_name": "Multi",
            "family_name": "User",
            "groups": ["beta-users"]
        }
        
        # Should update / sync user and membership without crashing, creating a new profile
        updated_user = backend.update_user(user, beta_claims)
        self.assertEqual(updated_user, user)
            
        # Verify membership and profile were created for tenant-beta
        self.assertTrue(user.asset_holder_profiles.filter(tenant=self.tenant_beta).exists())
        self.assertTrue(TenantMembership.objects.filter(user=user, tenant=self.tenant_beta).exists())

    def test_malformed_missing_oidc_claims(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        # Case A: absent 'upn' and absent 'email' (should fall back to sub or username@oidc)
        claims_only_sub = {
            "sub": "only-sub-123",
            "given_name": "Only",
            "family_name": "Sub",
        }
        user_only_sub = backend.create_user(claims_only_sub)
        self.assertIsNotNone(user_only_sub)
        self.assertEqual(user_only_sub.username, "only-sub-123")
        self.assertEqual(user_only_sub.email, "")
        
        # Verify AssetHolder profile was created with fallback UPN
        holder_sub = AssetHolder.objects.get(user=user_only_sub, tenant=self.tenant_alpha)
        self.assertEqual(holder_sub.upn, "only-sub-123@oidc")
        self.assertEqual(holder_sub.email, "")

        # Case B: empty email string in claims
        claims_empty_email = {
            "sub": "empty-email-sub",
            "email": "",
            "given_name": "Empty",
            "family_name": "Email",
        }
        user_empty_email = backend.create_user(claims_empty_email)
        self.assertIsNotNone(user_empty_email)
        self.assertEqual(user_empty_email.email, "")
        
        # Verify AssetHolder profile creation for empty email
        holder_empty_email = AssetHolder.objects.get(user=user_empty_email, tenant=self.tenant_alpha)
        self.assertEqual(holder_empty_email.upn, "empty-email-sub@oidc")
        self.assertEqual(holder_empty_email.email, "")

        # Case C: missing 'groups' claim completely
        claims_no_groups = {
            "sub": "no-groups-sub",
            "email": "nogroups@alpha.com",
        }
        user_no_groups = backend.create_user(claims_no_groups)
        membership_no_groups = TenantMembership.objects.get(user=user_no_groups, tenant=self.tenant_alpha)
        self.assertEqual(membership_no_groups.role.name, "Member") # Fallback to Member

        # Case D: malformed 'groups' claim (e.g. not a list or string, like a dict or integer)
        claims_dict_groups = {
            "sub": "dict-groups-sub",
            "email": "dictgroups@alpha.com",
            "groups": {"some": "dict"}
        }
        user_dict_groups = backend.create_user(claims_dict_groups)
        membership_dict_groups = TenantMembership.objects.get(user=user_dict_groups, tenant=self.tenant_alpha)
        self.assertEqual(membership_dict_groups.role.name, "Member") # Fallback to Member

        claims_int_groups = {
            "sub": "int-groups-sub",
            "email": "intgroups@alpha.com",
            "groups": 12345
        }
        user_int_groups = backend.create_user(claims_int_groups)
        membership_int_groups = TenantMembership.objects.get(user=user_int_groups, tenant=self.tenant_alpha)
        self.assertEqual(membership_int_groups.role.name, "Member") # Fallback to Member

    def test_upn_and_onetoone_collisions_multitenant(self):
        backend = TenantOIDCBackend()
        
        # User A in Tenant Alpha has UPN "coll@test.com"
        set_current_tenant(self.tenant_alpha)
        user_a = backend.create_user({
            "email": "usera@alpha.com",
            "sub": "sub-usera",
            "upn": "coll@test.com",
            "given_name": "User",
            "family_name": "A"
        })
        self.assertEqual(user_a.asset_holder_profiles.filter(tenant=self.tenant_alpha).first().upn, "coll@test.com")
        self.assertEqual(user_a.asset_holder_profiles.filter(tenant=self.tenant_alpha).first().tenant, self.tenant_alpha)

        # Now, try to create User B in Tenant Alpha with the same UPN "coll@test.com"
        # Since UPN has a unique constraint per tenant, this must fail at AssetHolder creation
        # but User B should still log in successfully (without AssetHolder profile)
        with self.assertLogs('core.auth.oidc', level='WARNING') as cm:
            user_b = backend.create_user({
                "email": "userb@alpha.com",
                "sub": "sub-userb",
                "upn": "coll@test.com",
                "given_name": "User",
                "family_name": "B"
            })
            self.assertIsNotNone(user_b)
            self.assertTrue(any("IntegrityError while creating AssetHolder" in line for line in cm.output))
        
        # Verify user_b was created, is in tenant_alpha, but has no asset_holder_profile
        user_b.refresh_from_db()
        self.assertFalse(user_b.asset_holder_profiles.filter(tenant=self.tenant_alpha).exists())
        self.assertTrue(TenantMembership.objects.filter(user=user_b, tenant=self.tenant_alpha).exists())

        # No OneToOne Constraint Collision:
        # A user already has a linked profile in Tenant Alpha. They now log into Tenant Beta.
        # It should succeed and create a new profile in Tenant Beta.
        set_current_tenant(self.tenant_beta)
        updated_user_a = backend.update_user(user_a, {
            "email": "usera@alpha.com",
            "sub": "sub-usera",
            "upn": "coll@test.com",
            "given_name": "User",
            "family_name": "A",
            "groups": ["beta-users"]
        })
        self.assertEqual(updated_user_a, user_a)
        
        # Verify user_a has a profile in Tenant Beta now
        user_a.refresh_from_db()
        self.assertTrue(user_a.asset_holder_profiles.filter(tenant=self.tenant_beta).exists())
        # Verify user_a has membership in Tenant Beta now
        self.assertTrue(TenantMembership.objects.filter(user=user_a, tenant=self.tenant_beta).exists())

    def test_group_priority_selection(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        # User is in both alpha-members (Member) and alpha-admins (Admin) and alpha-managers (Manager)
        # Should select Admin (highest priority)
        claims = {
            "email": "multi-group@alpha.com",
            "sub": "sub-multi-group",
            "groups": ["alpha-members", "alpha-admins", "alpha-managers"]
        }
        user = backend.create_user(claims)
        membership = TenantMembership.objects.get(user=user, tenant=self.tenant_alpha)
        self.assertEqual(membership.role.name, "Admin")

        # User is in alpha-members (Member) and alpha-managers (Manager)
        # Should select Manager
        claims_mgr = {
            "email": "multi-group-mgr@alpha.com",
            "sub": "sub-multi-group-mgr",
            "groups": ["alpha-members", "alpha-managers"]
        }
        user_mgr = backend.create_user(claims_mgr)
        membership_mgr = TenantMembership.objects.get(user=user_mgr, tenant=self.tenant_alpha)
        self.assertEqual(membership_mgr.role.name, "Manager")

    def test_group_name_lookup_case_sensitivity(self):
        backend = TenantOIDCBackend()
        set_current_tenant(self.tenant_alpha)

        # The mapping has "alpha-admins" but claims has "ALPHA-ADMINS"
        # Since group lookup is case-sensitive, it won't match "alpha-admins",
        # and will fall back to Member
        claims = {
            "email": "caps-group@alpha.com",
            "sub": "sub-caps-group",
            "groups": ["ALPHA-ADMINS"]
        }
        user = backend.create_user(claims)
        membership = TenantMembership.objects.get(user=user, tenant=self.tenant_alpha)
        self.assertEqual(membership.role.name, "Member")

    @override_settings(
        OIDC_OP_AUTHORIZATION_ENDPOINT="https://example.com/oauth2",
        OIDC_RP_CLIENT_ID="global-client-id",
        OIDC_RP_CLIENT_SECRET="global-secret",
    )
    def test_authorize_view_missing_tenant_slug(self):
        # Access authenticate view without a tenant parameter in GET or URL path
        url = reverse('oidc_authentication_init')
        response = self.client.get(url)
        # Without any tenant context, it should fall back to global/defaults
        self.assertEqual(response.status_code, 302)
        # The fallback auth endpoint is https://example.com/oauth2
        self.assertIn("https://example.com/oauth2", response.url)

    def test_callback_view_invalid_session_tenant_slug(self):
        # Put an invalid tenant slug in the session
        session = self.client.session
        session['oidc_states'] = {
            'state-456': {
                'nonce': 'nonce-456',
                'code_verifier': 'verifier-456'
            }
        }
        session['oidc_tenant_slug'] = 'invalid-tenant-slug'
        session.save()

        # Access callback view
        url = reverse('oidc_authentication_callback') + "?code=code-456&state=state-456"
        # It should not crash on invalid/missing tenant; it should proceed (and fail authentication or handle it gracefully)
        with patch('django.contrib.auth.authenticate') as mock_authenticate:
            mock_authenticate.return_value = None
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
