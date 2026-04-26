import datetime
from django.test import TestCase
from django.utils import timezone
from subscriptions.models import Provider, Subscription, SubscriptionStatusChoices

class SubscriptionFilterSetTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="AWS", is_active=True)
        self.provider2 = Provider.objects.create(name="GCP", is_active=True)
        self.today = timezone.now().date()
        self.sub_active = Subscription.objects.create(
            name="Active Sub", provider=self.provider, status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.today + datetime.timedelta(days=30),
        )
        self.sub_expired = Subscription.objects.create(
            name="Expired Sub", provider=self.provider2, status=SubscriptionStatusChoices.EXPIRED,
            renewal_date=self.today - datetime.timedelta(days=10),
        )

    def test_filter_by_status(self):
        from subscriptions.filters import SubscriptionFilterSet
        f = SubscriptionFilterSet({'status': 'active'}, queryset=Subscription.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_active, f.qs)
        self.assertNotIn(self.sub_expired, f.qs)

    def test_filter_by_provider(self):
        from subscriptions.filters import SubscriptionFilterSet
        f = SubscriptionFilterSet(
            {'provider': self.provider.pk},
            queryset=Subscription.objects.all(),
        )
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_active, f.qs)
        self.assertNotIn(self.sub_expired, f.qs)

    def test_filter_search(self):
        from subscriptions.filters import SubscriptionFilterSet
        f = SubscriptionFilterSet({'q': 'Expired'}, queryset=Subscription.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_expired, f.qs)
        self.assertNotIn(self.sub_active, f.qs)

    def test_filter_renewal_within(self):
        from subscriptions.filters import SubscriptionFilterSet
        f = SubscriptionFilterSet(
            {'renewal_within': '60'},
            queryset=Subscription.objects.all(),
        )
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_active, f.qs)
