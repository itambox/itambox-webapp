from datetime import date, timedelta
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from core.models import Notification
from core.tests.mixins import grant
from organization.models import TenantGroup, Tenant, Site, Location
from assets.models import Asset
from subscriptions.models import (
    Provider, Subscription, SubscriptionAssignment,
    SubscriptionTypeChoices, SubscriptionStatusChoices, BillingCycleChoices
)
from subscriptions.tasks import check_subscription_expiries_and_reminders

User = get_user_model()


class SubscriptionFixesTests(TestCase):
    def setUp(self):
        self.client = Client()
        
        # Create tenants
        self.tg = TenantGroup.objects.create(name="Group 1", slug="g1")
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a", group=self.tg)
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b", group=self.tg)
        
        # Create users
        self.super_user = User.objects.create_user(
            username="super", password="password", is_staff=True, is_superuser=True
        )
        self.user_a = User.objects.create_user(
            username="user_a", password="password", is_staff=False
        )
        self.user_b = User.objects.create_user(
            username="user_b", password="password", is_staff=False
        )
        
        # Create roles
        from organization.models import Role
        self.role_a = Role.objects.create(
            tenant=self.tenant_a,
            name="Role A",
            permissions=[
                'subscriptions.change_subscription',
                'subscriptions.add_subscription',
                'subscriptions.add_subscriptionassignment',
            ]
        )
        self.role_b = Role.objects.create(
            tenant=self.tenant_b,
            name="Role B",
            permissions=[]
        )

        # Create memberships
        grant(self.user_a, self.tenant_a, self.role_a)
        grant(self.user_b, self.tenant_b, self.role_b)
        # super_user is a platform operator who is also a member of tenant_a, so it
        # receives tenant_a's subscription notifications. Expiry/reminder recipients
        # are scoped to staff who are MEMBERS of the subscription's tenant (B7) —
        # a bare is_staff user with no membership is no longer notified.
        grant(self.super_user, self.tenant_a, self.role_a)


        # Create providers and subscriptions
        self.provider_a = Provider.objects.create(name="Provider A", tenant=self.tenant_a)
        self.provider_b = Provider.objects.create(name="Provider B", tenant=self.tenant_b)
        
        self.sub_a = Subscription.objects.create(
            name="Subscription A",
            provider=self.provider_a,
            tenant=self.tenant_a,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=date.today() + timedelta(days=10),
            renewal_cost=100.00,
            currency="USD",
            billing_cycle=BillingCycleChoices.MONTHLY
        )
        self.sub_b = Subscription.objects.create(
            name="Subscription B",
            provider=self.provider_b,
            tenant=self.tenant_b,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=date.today() + timedelta(days=10),
            renewal_cost=200.00,
            currency="USD",
            billing_cycle=BillingCycleChoices.MONTHLY
        )

        # Add permissions
        self.ct_sub = ContentType.objects.get_for_model(Subscription)
        self.perm_change = Permission.objects.get(codename="change_subscription", content_type=self.ct_sub)
        self.perm_add = Permission.objects.get(codename="add_subscription", content_type=self.ct_sub)
        
        self.ct_assign = ContentType.objects.get_for_model(SubscriptionAssignment)
        self.perm_add_assign = Permission.objects.get(codename="add_subscriptionassignment", content_type=self.ct_assign)

        self.user_a.user_permissions.add(self.perm_change, self.perm_add, self.perm_add_assign)

    def test_permission_checks_on_lifecycle_views(self):
        # User without change_subscription permission should get 403 Forbidden
        self.client.login(username="user_b", password="password")
        
        renew_url = reverse("subscriptions:subscription_renew", kwargs={"pk": self.sub_b.pk})
        cancel_url = reverse("subscriptions:subscription_cancel", kwargs={"pk": self.sub_b.pk})
        suspend_url = reverse("subscriptions:subscription_suspend", kwargs={"pk": self.sub_b.pk})
        checkout_url = reverse("subscriptions:subscription_checkout", kwargs={"pk": self.sub_b.pk})
        
        # Test renew
        self.assertEqual(self.client.get(renew_url).status_code, 403)
        self.assertEqual(self.client.post(renew_url, {"renewal_date": "2027-01-01"}).status_code, 403)
        
        # Test cancel
        self.assertEqual(self.client.get(cancel_url).status_code, 403)
        self.assertEqual(self.client.post(cancel_url, {"cancellation_date": "2026-06-01", "reason": "test"}).status_code, 403)
        
        # Test suspend
        self.assertEqual(self.client.post(suspend_url).status_code, 403)
        
        # Test checkout
        self.assertEqual(self.client.get(checkout_url).status_code, 403)
        self.assertEqual(self.client.post(checkout_url, {"target_type": "location"}).status_code, 403)

        # User assignment create view check
        assign_create_url = reverse("subscriptions:subscriptionassignment_create")
        self.assertEqual(self.client.get(f"{assign_create_url}?content_type=1&object_id=1").status_code, 403)

    def test_tenant_scoping_on_lifecycle_views(self):
        # User A has change_subscription permission but belongs to Tenant A.
        # User A should NOT be able to access Tenant B's subscription, resulting in a 404.
        self.client.login(username="user_a", password="password")
        
        renew_url = reverse("subscriptions:subscription_renew", kwargs={"pk": self.sub_b.pk})
        cancel_url = reverse("subscriptions:subscription_cancel", kwargs={"pk": self.sub_b.pk})
        suspend_url = reverse("subscriptions:subscription_suspend", kwargs={"pk": self.sub_b.pk})
        checkout_url = reverse("subscriptions:subscription_checkout", kwargs={"pk": self.sub_b.pk})
        
        self.assertEqual(self.client.get(renew_url).status_code, 404)
        self.assertEqual(self.client.post(renew_url, {"renewal_date": "2027-01-01"}).status_code, 404)
        self.assertEqual(self.client.get(cancel_url).status_code, 404)
        self.assertEqual(self.client.post(cancel_url, {"cancellation_date": "2026-06-01", "reason": "test"}).status_code, 404)
        self.assertEqual(self.client.post(suspend_url).status_code, 404)
        self.assertEqual(self.client.get(checkout_url).status_code, 404)
        self.assertEqual(self.client.post(checkout_url, {"target_type": "location"}).status_code, 404)

    def test_duplicate_assignment_validation(self):
        # Set up a target object
        site = Site.objects.create(name="Site A", slug="site-a", tenant=self.tenant_a)
        location = Location.objects.create(name="Loc A", slug="loc-a", site=site, tenant=self.tenant_a)
        loc_ct = ContentType.objects.get_for_model(Location)
        
        # Create initial assignment
        SubscriptionAssignment.objects.create(
            subscription=self.sub_a,
            content_type=loc_ct,
            object_id=location.pk
        )
        
        # Login User A who has permissions
        self.client.login(username="user_a", password="password")
        
        # Test Checkout View Form duplicate check
        checkout_url = reverse("subscriptions:subscription_checkout", kwargs={"pk": self.sub_a.pk})
        resp = self.client.post(checkout_url, {
            "target_type": "location",
            "location": location.pk
        })
        self.assertEqual(resp.status_code, 200) # Form re-renders on error
        self.assertFormError(resp.context['form'], None, f"This subscription is already assigned to {location}.")

        # Test Assignment Create View duplicate check
        assign_create_url = reverse("subscriptions:subscriptionassignment_create")
        post_url = f"{assign_create_url}?content_type={loc_ct.pk}&object_id={location.pk}"
        resp = self.client.post(post_url, {
            "subscription": self.sub_a.pk
        })
        self.assertEqual(resp.status_code, 200) # Form re-renders on error
        self.assertFormError(resp.context['form'], None, "This subscription is already assigned to this object.")

    def test_background_task_expiry_and_reminders(self):
        # Create a subscription that has expired
        sub_expired = Subscription.objects.create(
            name="Expired Sub",
            provider=self.provider_a,
            tenant=self.tenant_a,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=date.today() + timedelta(days=10),
            renewal_cost=100.00
        )
        # Bypass pre_save signal using .update() to set renewal_date to the past
        Subscription.objects.filter(pk=sub_expired.pk).update(
            renewal_date=date.today() - timedelta(days=1)
        )
        # Create subscriptions approaching renewal (30, 14, 7 days)
        sub_30 = Subscription.objects.create(
            name="Sub 30",
            provider=self.provider_a,
            tenant=self.tenant_a,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=date.today() + timedelta(days=30),
            renewal_cost=100.00
        )
        sub_14 = Subscription.objects.create(
            name="Sub 14",
            provider=self.provider_a,
            tenant=self.tenant_a,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=date.today() + timedelta(days=14),
            renewal_cost=100.00
        )
        sub_7 = Subscription.objects.create(
            name="Sub 7",
            provider=self.provider_a,
            tenant=self.tenant_a,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=date.today() + timedelta(days=7),
            renewal_cost=100.00
        )
        
        # Clear existing notifications
        Notification.objects.all().delete()
        
        # Run background task
        check_subscription_expiries_and_reminders()
        
        # Refresh expired sub
        sub_expired.refresh_from_db()
        self.assertEqual(sub_expired.status, SubscriptionStatusChoices.EXPIRED)
        
        # Check that notifications were created
        notifications = Notification.objects.all()
        subjects = [n.subject for n in notifications]

        
        # Expecting notifications for:
        # - Expired Sub
        # - Sub 30
        # - Sub 14
        # - Sub 7
        # Note: notifications go to staff who are members of the subscription's
        # tenant (B7). self.super_user is staff AND a member of tenant_a.
        self.assertTrue(any("Subscription Expired: Expired Sub" in s for s in subjects))
        self.assertTrue(any("Subscription Renewal Warning: Sub 30 in 30 Days" in s for s in subjects))
        self.assertTrue(any("Subscription Renewal Warning: Sub 14 in 14 Days" in s for s in subjects))
        self.assertTrue(any("Subscription Renewal Warning: Sub 7 in 7 Days" in s for s in subjects))
