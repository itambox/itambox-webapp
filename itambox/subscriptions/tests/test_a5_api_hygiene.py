"""A5 API-hygiene regression tests.

Covers two audit findings:

- WS3-3: the ``update_status`` @action now routes through the same optimistic-
  concurrency (If-Match/ETag) machinery as the standard ``update()``. A stale
  If-Match yields 412, and a successful status write records an accurate
  change-log diff (old status -> new status).
- WS3-7b: the 409 ProtectedError body reports only a COUNT of blocking
  dependents, never the str()/pk of related rows the caller may not be entitled
  to see.
"""
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import ObjectChange
from core.choices import ObjectChangeActionChoices
from core.tests.mixins import grant
from organization.models import (
    TenantGroup, Tenant, AssetHolder, Role,
)
from subscriptions.models import (
    Provider, Subscription,
    SubscriptionTypeChoices, SubscriptionStatusChoices, BillingCycleChoices,
)

User = get_user_model()


class A5ApiHygieneTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='a5_staff', email='a5_staff@example.com',
            password='password123', is_staff=True, is_superuser=False,
        )

        self.tg = TenantGroup.objects.create(name="A5 TG", slug="a5-tg")
        self.tenant = Tenant.objects.create(name="A5 Tenant", slug="a5-tenant", group=self.tg)
        # The AssetHolder profile gives StrictTenantPermission a resolvable
        # tenant scope under force_authenticate (no session).
        AssetHolder.objects.create(
            user=self.staff, first_name="A5", last_name="Staff",
            upn="a5.staff", email="a5_staff@example.com", tenant=self.tenant,
        )

        # Tenant-scoped provider + subscription (PROTECT relation lets us exercise
        # the 409 ProtectedError handler).
        self.provider = Provider.objects.create(
            name="A5 Provider", slug="a5-provider", tenant=self.tenant,
        )
        self.subscription = Subscription.objects.create(
            name="A5 Subscription",
            provider=self.provider,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=10.00,
            currency="USD",
            billing_cycle=BillingCycleChoices.MONTHLY,
            licensed_quantity=5,
            tenant=self.tenant,
        )

        role = Role.objects.create(
            tenant=self.tenant,
            name='A5 Role',
            permissions=[
                'subscriptions.view_provider', 'subscriptions.delete_provider',
                'subscriptions.view_subscription', 'subscriptions.change_subscription',
                'subscriptions.delete_subscription',
            ],
        )
        grant(self.staff, self.tenant, role)

    # ----- WS3-3 -----------------------------------------------------------

    @staticmethod
    def _etag_for(obj):
        # Mirror ETagMixin._get_etag: a weak ETag derived from updated_at. GET
        # responses don't carry the header, so derive the current token the same
        # way the server does for mutating requests.
        obj.refresh_from_db()
        return f'W/"{obj.updated_at.isoformat()}"'

    def test_update_status_requires_if_match(self):
        """A status write with no If-Match is refused (428), not last-writer-wins."""
        self.client.force_authenticate(user=self.staff)
        url = reverse(
            'api:subscriptions_api:subscription-update-status',
            kwargs={'pk': self.subscription.pk},
        )
        response = self.client.patch(url, data={'status': 'suspended'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_428_PRECONDITION_REQUIRED)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, SubscriptionStatusChoices.ACTIVE)

    def test_update_status_stale_if_match_yields_412(self):
        """A stale (no-longer-current) If-Match loses the concurrency race."""
        self.client.force_authenticate(user=self.staff)
        url = reverse(
            'api:subscriptions_api:subscription-update-status',
            kwargs={'pk': self.subscription.pk},
        )
        response = self.client.patch(
            url, data={'status': 'suspended'}, format='json',
            HTTP_IF_MATCH='W/"1999-01-01T00:00:00+00:00"',
        )
        self.assertEqual(response.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, SubscriptionStatusChoices.ACTIVE)

    def test_update_status_records_accurate_changelog_diff(self):
        """A valid status write records an UPDATE ObjectChange with the real diff."""
        self.client.force_authenticate(user=self.staff)
        etag = self._etag_for(self.subscription)

        ct = ContentType.objects.get_for_model(Subscription)
        before = ObjectChange.objects.filter(
            changed_object_type=ct, changed_object_id=self.subscription.pk,
        ).count()

        url = reverse(
            'api:subscriptions_api:subscription-update-status',
            kwargs={'pk': self.subscription.pk},
        )
        response = self.client.patch(
            url, data={'status': 'suspended'}, format='json', HTTP_IF_MATCH=etag,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'suspended')
        # The ETag advanced with the write, so the response carries a fresh token.
        self.assertIn('ETag', response)
        self.assertNotEqual(response['ETag'], etag)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, SubscriptionStatusChoices.SUSPENDED)

        change = ObjectChange.objects.filter(
            changed_object_type=ct, changed_object_id=self.subscription.pk,
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        ).order_by('-time').first()
        self.assertIsNotNone(change, "update_status must record an ObjectChange")
        # The snapshot()/save() path produced an accurate old->new status diff.
        self.assertEqual(change.prechange_data.get('status'), SubscriptionStatusChoices.ACTIVE)
        self.assertEqual(change.postchange_data.get('status'), SubscriptionStatusChoices.SUSPENDED)
        # Exactly one new change row for this write.
        after = ObjectChange.objects.filter(
            changed_object_type=ct, changed_object_id=self.subscription.pk,
        ).count()
        self.assertEqual(after, before + 1)

    # ----- WS3-7b ----------------------------------------------------------

    def test_protected_error_409_reports_count_not_pks(self):
        """Deleting a provider that a subscription PROTECTs returns a 409 whose
        body carries a COUNT of dependents, not the enumerated str()/pk."""
        self.client.force_authenticate(user=self.staff)
        provider_detail = reverse(
            'api:subscriptions_api:provider-detail', kwargs={'pk': self.provider.pk},
        )
        etag = self._etag_for(self.provider)

        response = self.client.delete(provider_detail, HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, 409)
        detail = response.data['detail']
        # A count is present.
        self.assertIn('1 dependent', detail)
        # The blocking subscription's repr / pk is NOT leaked in the body.
        self.assertNotIn(str(self.subscription), detail)
        self.assertNotIn(f'({self.subscription.pk})', detail)
