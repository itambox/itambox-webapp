from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal

from assets.models import (
    Asset, AssetType, AssetRequest, StatusLabel, AssetRole, Manufacturer, Category
)
from organization.models import AssetHolder, Site, Location, Tenant, TenantRole, TenantMembership
from assets.views.request_views import approve_asset_request, deny_asset_request
from assets.services import checkout_asset
from core.managers import set_current_tenant, set_current_membership

User = get_user_model()


class RequisitionSystemTestCase(TestCase):
    def setUp(self):
        # Create Tenant
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme")

        # Create users
        self.admin = User.objects.create_user(
            username='adminuser', password='password123', is_staff=True, is_superuser=True
        )
        self.staff = User.objects.create_user(
            username='staffuser', password='password123', is_staff=True, is_superuser=False
        )
        self.requester_user = User.objects.create_user(
            username='requesteruser', password='password123', is_staff=False, is_superuser=False
        )
        self.other_user = User.objects.create_user(
            username='otheruser', password='password123', is_staff=False, is_superuser=False
        )

        # Create Tenant Memberships
        self.role_standard = TenantRole.objects.create(
            tenant=self.tenant,
            name="Standard Employee",
            permissions=["assets.add_assetrequest", "assets.view_assetrequest"]
        )
        self.role_delegated = TenantRole.objects.create(
            tenant=self.tenant,
            name="Helpdesk Technician",
            permissions=[
                "assets.add_assetrequest",
                "assets.view_assetrequest",
                "assets.add_delegated_assetrequest"
            ]
        )

        TenantMembership.objects.create(
            user=self.requester_user,
            tenant=self.tenant,
            role=self.role_standard
        )
        TenantMembership.objects.create(
            user=self.other_user,
            tenant=self.tenant,
            role=self.role_delegated
        )

        # Set active tenant thread-local context
        set_current_tenant(self.tenant)

        # Create Manufacturer
        self.manufacturer = Manufacturer.objects.create(name="Lenovo", slug="lenovo")

        # Create Asset Role
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")

        # Create Status Labels
        self.status_deployable = StatusLabel.objects.create(
            name="Deployable", slug="deployable", type=StatusLabel.TYPE_DEPLOYABLE
        )
        self.status_deployed = StatusLabel.objects.create(
            name="Deployed", slug="deployed", type=StatusLabel.TYPE_DEPLOYED
        )

        # Create Asset Types
        self.type_requestable = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad T14",
            slug="thinkpad-t14",
            requestable=True
        )
        self.type_not_requestable = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad T16",
            slug="thinkpad-t16",
            requestable=False
        )

        # Create Assets
        self.asset_requestable = Asset.objects.create(
            name="ThinkPad T14-001",
            asset_tag="TAG-001",
            asset_type=self.type_requestable,
            asset_role=self.role,
            status=self.status_deployable,
            requestable=True,
            tenant=self.tenant
        )
        self.asset_not_requestable = Asset.objects.create(
            name="ThinkPad T14-002",
            asset_tag="TAG-002",
            asset_type=self.type_requestable,
            asset_role=self.role,
            status=self.status_deployable,
            requestable=False,
            tenant=self.tenant
        )
        # Asset that inherits True from type_requestable
        self.asset_inherited_requestable = Asset.objects.create(
            name="ThinkPad T14-003",
            asset_tag="TAG-003",
            asset_type=self.type_requestable,
            asset_role=self.role,
            status=self.status_deployable,
            requestable=None,
            tenant=self.tenant
        )
        # Asset that inherits False from type_not_requestable
        self.asset_inherited_not_requestable = Asset.objects.create(
            name="ThinkPad T16-003",
            asset_tag="TAG-004",
            asset_type=self.type_not_requestable,
            asset_role=self.role,
            status=self.status_deployable,
            requestable=None,
            tenant=self.tenant
        )
        # Asset that overrides type_not_requestable to True
        self.asset_override_requestable = Asset.objects.create(
            name="ThinkPad T16-004",
            asset_tag="TAG-005",
            asset_type=self.type_not_requestable,
            asset_role=self.role,
            status=self.status_deployable,
            requestable=True,
            tenant=self.tenant
        )

        # Create Asset Holder Profile for the requester
        self.holder = AssetHolder.objects.create(
            user=self.requester_user,
            first_name="Jane",
            last_name="Requester",
            upn="jane@example.com",
            tenant=self.tenant
        )

        # Create Site and Location for checkout targets
        self.site = Site.objects.create(name="Main HQ", slug="main-hq", tenant=self.tenant)
        self.location = Location.objects.create(name="Staging Room", slug="staging", site=self.site, tenant=self.tenant)

    def test_request_creation_and_constraints(self):
        # 1. Valid request for requestable type only
        req1 = AssetRequest.objects.create(
            requester=self.requester_user,
            asset_type=self.type_requestable,
            notes="Need a development laptop"
        )
        self.assertEqual(req1.status, AssetRequest.STATUS_PENDING)

        # 2. Valid request for requestable asset only
        req2 = AssetRequest.objects.create(
            requester=self.requester_user,
            asset=self.asset_requestable,
            notes="Need a specific laptop"
        )
        self.assertEqual(req2.status, AssetRequest.STATUS_PENDING)

        # 3. Requesting an unrequestable asset type should fail
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.requester_user,
                asset_type=self.type_not_requestable
            )

        # 4. Requesting an unrequestable asset should fail
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.requester_user,
                asset=self.asset_not_requestable
            )

        # 5. Requesting an asset that doesn't match the asset type should fail
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.requester_user,
                asset_type=self.type_not_requestable,
                asset=self.asset_requestable
            )

        # 6. Requesting without both asset and asset type should fail
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.requester_user
            )

    def test_auto_fulfillment_on_checkout(self):
        # Create a pending request for a generic type
        req = AssetRequest.objects.create(
            requester=self.requester_user,
            asset_type=self.type_requestable,
            notes="Need a laptop"
        )

        self.assertEqual(req.status, AssetRequest.STATUS_PENDING)

        # Check out the asset to the requester (holder)
        # Using checkout_asset service which triggers signals
        checkout_asset(
            asset=self.asset_requestable,
            holder=self.holder,
            user=self.admin,
            notes="Checking out standard laptop"
        )

        # Refresh request from DB
        req.refresh_from_db()
        self.assertEqual(req.status, AssetRequest.STATUS_FULFILLED)
        self.assertEqual(req.asset, self.asset_requestable)
        self.assertEqual(req.responded_by, self.admin)
        self.assertIsNotNone(req.response_date)
        self.assertIn("Automatically fulfilled", req.response_notes)

    def test_auto_fulfillment_on_specific_checkout(self):
        # Create a pending request for a specific asset
        req = AssetRequest.objects.create(
            requester=self.requester_user,
            asset=self.asset_requestable,
            notes="Need this exact laptop"
        )

        # Check out to holder
        checkout_asset(
            asset=self.asset_requestable,
            holder=self.holder,
            user=self.admin,
            notes="Here you go"
        )

        # Refresh request from DB
        req.refresh_from_db()
        self.assertEqual(req.status, AssetRequest.STATUS_FULFILLED)
        self.assertEqual(req.asset, self.asset_requestable)

    def test_approve_asset_request_workflow(self):
        req = AssetRequest.objects.create(
            requester=self.requester_user,
            asset_type=self.type_requestable,
            notes="Laptop request"
        )

        # Staff user approves the request
        approve_asset_request(
            request_instance=req,
            user=self.staff,
            allocated_asset=self.asset_requestable,
            response_notes="Approved for collection"
        )

        req.refresh_from_db()
        self.assertEqual(req.status, AssetRequest.STATUS_APPROVED)
        self.assertEqual(req.responded_by, self.staff)
        self.assertEqual(req.asset, self.asset_requestable)
        self.assertEqual(req.response_notes, "Approved for collection")

    def test_deny_asset_request_workflow(self):
        req = AssetRequest.objects.create(
            requester=self.requester_user,
            asset_type=self.type_requestable,
            notes="Laptop request"
        )

        # Staff user denies the request
        deny_asset_request(
            request_instance=req,
            user=self.staff,
            response_notes="Out of budget"
        )

        req.refresh_from_db()
        self.assertEqual(req.status, AssetRequest.STATUS_DENIED)
        self.assertEqual(req.responded_by, self.staff)
        self.assertEqual(req.response_notes, "Out of budget")

    def test_views_endpoints(self):
        # Log in as admin
        self.client.login(username='adminuser', password='password123')

        # 1. Create a request via view POST
        post_data = {
            'asset_type': self.type_requestable.pk,
            'asset': '',
            'notes': 'Requested via UI'
        }
        response = self.client.post(reverse('assets:request_create'), data=post_data)
        self.assertEqual(response.status_code, 302) # Success redirect

        # Verify created
        req = AssetRequest.objects.get(notes='Requested via UI')
        self.assertEqual(req.requester, self.admin) # Auto-set to current user

        # 2. Detail View
        response = self.client.get(reverse('assets:request_detail', kwargs={'pk': req.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Requested via UI')

        # 3. List View
        response = self.client.get(reverse('assets:request_list'))
        self.assertEqual(response.status_code, 200)

        # Log in as regular user
        self.client.login(username='requesteruser', password='password123')
        # 4. Regular user can cancel their request
        user_req = AssetRequest.objects.create(
            requester=self.requester_user,
            asset_type=self.type_requestable,
            notes="Self-cancel test",
            tenant=self.tenant
        )
        response = self.client.post(reverse('assets:request_cancel', kwargs={'pk': user_req.pk}))
        self.assertEqual(response.status_code, 302) # Redirects
 
        user_req.refresh_from_db()
        self.assertEqual(user_req.status, AssetRequest.STATUS_CANCELLED)
 
        # 5. Regular user cannot cancel other user's request
        other_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad X1",
            slug="thinkpad-x1",
            requestable=True
        )
        admin_req = AssetRequest.objects.create(
            requester=self.admin,
            asset_type=other_type,
            notes="Admin request",
            tenant=self.tenant
        )
        response = self.client.post(reverse('assets:request_cancel', kwargs={'pk': admin_req.pk}))
        self.assertEqual(response.status_code, 403)
        admin_req.refresh_from_db()
        self.assertNotEqual(admin_req.status, AssetRequest.STATUS_CANCELLED)
 
    def test_request_inheritance_and_overrides(self):
        # 1. Asset inheriting True should succeed
        req_inherited_ok = AssetRequest.objects.create(
            requester=self.requester_user,
            asset=self.asset_inherited_requestable,
            notes="Inherited requestable",
            tenant=self.tenant
        )
        self.assertEqual(req_inherited_ok.status, AssetRequest.STATUS_PENDING)
 
        # 2. Asset inheriting False should fail
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.other_user,
                asset=self.asset_inherited_not_requestable,
                tenant=self.tenant
            )
 
        # 3. Asset overriding False to True should succeed
        req_override_ok = AssetRequest.objects.create(
            requester=self.other_user,
            asset=self.asset_override_requestable,
            tenant=self.tenant
        )
        self.assertEqual(req_override_ok.status, AssetRequest.STATUS_PENDING)
 
    def test_double_request_and_status_constraints(self):
        # 1. Create initial request
        AssetRequest.objects.create(
            requester=self.requester_user,
            asset=self.asset_requestable,
            tenant=self.tenant
        )
 
        # Double requesting same specific asset should fail
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.requester_user,
                asset=self.asset_requestable,
                tenant=self.tenant
            )
 
        # Double requesting same type should fail
        AssetRequest.objects.create(
            requester=self.other_user,
            asset_type=self.type_requestable,
            tenant=self.tenant
        )
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.other_user,
                asset_type=self.type_requestable,
                tenant=self.tenant
            )
 
        # Requesting non-deployable asset should fail
        non_deployable_asset = Asset.objects.create(
            name="Broken ThinkPad",
            asset_tag="TAG-009",
            asset_type=self.type_requestable,
            status=self.status_deployed, # Deployed status type is 'deployed', not 'deployable'
            requestable=True,
            tenant=self.tenant
        )
        with self.assertRaises(ValidationError):
            AssetRequest.objects.create(
                requester=self.other_user,
                asset=non_deployable_asset,
                tenant=self.tenant
            )

    def test_delegated_request_targets_and_constraints(self):
        # 1. Valid request delegated to user
        req_user = AssetRequest.objects.create(
            requester=self.other_user,
            asset_type=self.type_requestable,
            assigned_user=self.holder,
            tenant=self.tenant
        )
        self.assertEqual(req_user.assigned_target, self.holder)
        self.assertEqual(req_user.assigned_to_type, 'assetholder')
        req_user.delete()

        # 2. Valid request delegated to location
        req_loc = AssetRequest.objects.create(
            requester=self.other_user,
            asset_type=self.type_requestable,
            assigned_location=self.location,
            tenant=self.tenant
        )
        self.assertEqual(req_loc.assigned_target, self.location)
        self.assertEqual(req_loc.assigned_to_type, 'location')
        req_loc.delete()

        # 3. Valid request delegated to parent asset
        req_asset = AssetRequest.objects.create(
            requester=self.other_user,
            asset_type=self.type_requestable,
            assigned_asset=self.asset_requestable,
            tenant=self.tenant
        )
        self.assertEqual(req_asset.assigned_target, self.asset_requestable)
        self.assertEqual(req_asset.assigned_to_type, 'asset')
        req_asset.delete()

        # 4. Request with multiple targets should fail CheckConstraint / validation
        from django.db import IntegrityError
        with self.assertRaises((ValidationError, IntegrityError)):
            req_invalid = AssetRequest(
                requester=self.other_user,
                asset_type=self.type_requestable,
                assigned_user=self.holder,
                assigned_location=self.location,
                tenant=self.tenant
            )
            req_invalid.full_clean()
            req_invalid.save()

    def test_delegation_permissions(self):
        from assets.forms.request_forms import AssetRequestForm
        from django.test import RequestFactory
        
        factory = RequestFactory()
        
        # Test form validation for standard user (cannot delegate)
        request = factory.post('/')
        request.user = self.requester_user
        
        form_data = {
            'asset_type': self.type_requestable.pk,
            'target_type': 'location',
            'assigned_location': self.location.pk
        }
        form = AssetRequestForm(data=form_data, request=request)
        self.assertFalse(form.is_valid())
        self.assertIn("target_type", form.errors)

        # Test form validation for delegated user (can delegate)
        request_delegated = factory.post('/')
        request_delegated.user = self.other_user
        form_delegated = AssetRequestForm(data=form_data, request=request_delegated)
        self.assertTrue(form_delegated.is_valid())

    def test_prefilled_checkout_and_fulfillment(self):
        # Create an approved request delegated to a location
        req = AssetRequest.objects.create(
            requester=self.other_user,
            asset_type=self.type_requestable,
            assigned_location=self.location,
            status=AssetRequest.STATUS_APPROVED,
            asset=self.asset_requestable,
            tenant=self.tenant
        )

        from assets.views.asset_views import AssetCheckoutView
        from django.test import RequestFactory
        factory = RequestFactory()
        
        request = factory.get(f'/?request_id={req.pk}')
        request.user = self.admin
        
        view = AssetCheckoutView()
        view.request = request
        view.kwargs = {'pk': self.asset_requestable.pk}
        
        kwargs = view.get_form_kwargs()
        self.assertIn('initial', kwargs)
        self.assertEqual(kwargs['initial']['target_type'], 'location')
        self.assertEqual(kwargs['initial']['location'], self.location)

        # Test form submission auto-fulfills request
        post_data = {
            'target_type': 'location',
            'location': self.location.pk,
            'notes': 'Checkout notes'
        }
        post_request = factory.post(f'/?request_id={req.pk}', data=post_data)
        post_request.user = self.admin
        post_request.headers = {'HX-Request': 'true'}
        
        from assets.forms.checkout_forms import AssetCheckOutForm
        form = AssetCheckOutForm(data=post_data, asset=self.asset_requestable)
        self.assertTrue(form.is_valid())
        
        view_post = AssetCheckoutView()
        view_post.request = post_request
        view_post.kwargs = {'pk': self.asset_requestable.pk}
        
        response = view_post.form_valid(form)
        self.assertEqual(response.status_code, 200) # HTMX HX-Redirect response

        # Check request status
        req.refresh_from_db()
        self.assertEqual(req.status, AssetRequest.STATUS_FULFILLED)
        self.assertEqual(req.asset, self.asset_requestable)
