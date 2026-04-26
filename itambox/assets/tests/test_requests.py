from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal

from assets.models import (
    Asset, AssetType, AssetRequest, StatusLabel, AssetRole, Manufacturer, Category
)
from organization.models import AssetHolder, Site, Location
from assets.views.request_views import approve_asset_request, deny_asset_request
from assets.services import checkout_asset

User = get_user_model()


class RequisitionSystemTestCase(TestCase):
    def setUp(self):
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
            requestable=True
        )
        self.asset_not_requestable = Asset.objects.create(
            name="ThinkPad T14-002",
            asset_tag="TAG-002",
            asset_type=self.type_requestable,
            asset_role=self.role,
            status=self.status_deployable,
            requestable=False
        )

        # Create Asset Holder Profile for the requester
        self.holder = AssetHolder.objects.create(
            user=self.requester_user,
            first_name="Jane",
            last_name="Requester",
            upn="jane@example.com"
        )

        # Create Site and Location for checkout targets
        self.site = Site.objects.create(name="Main HQ", slug="main-hq")
        self.location = Location.objects.create(name="Staging Room", slug="staging", site=self.site)

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
            notes="Self-cancel test"
        )
        response = self.client.post(reverse('assets:request_cancel', kwargs={'pk': user_req.pk}))
        self.assertEqual(response.status_code, 302) # Redirects

        user_req.refresh_from_db()
        self.assertEqual(user_req.status, AssetRequest.STATUS_CANCELLED)

        # 5. Regular user cannot cancel other user's request
        admin_req = AssetRequest.objects.create(
            requester=self.admin,
            asset_type=self.type_requestable,
            notes="Admin request"
        )
        response = self.client.post(reverse('assets:request_cancel', kwargs={'pk': admin_req.pk}))
        self.assertEqual(response.status_code, 403)
        admin_req.refresh_from_db()
        self.assertNotEqual(admin_req.status, AssetRequest.STATUS_CANCELLED)
