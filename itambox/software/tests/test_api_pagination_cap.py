"""D8: the API paginator caps the row count (instead of an unbounded COUNT(*) on
every list page) and exposes a cursor mode that skips the count entirely.
"""
from django.test import override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from organization.models import Tenant, Role
from assets.models import Manufacturer
from software.models import Software
from core.tests.mixins import grant

# Bake the view queryset under a clean (no-tenant) import — see the note in
# inventory/tests/test_checkout_permissions.py.
import software.api.views  # noqa: F401,E402

User = get_user_model()


class ApiCountCapTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Tenant A', slug='capt-a')
        self.user = User.objects.create_user(username='capt_member', password='pw')
        role = Role.objects.create(
            tenant=self.tenant, name='R', permissions=['software.view_software']
        )
        grant(self.user, self.tenant, role)
        mfr = Manufacturer.objects.create(name='MS', slug='capt-ms')
        for i in range(3):
            Software.objects.create(name=f'SW{i}', manufacturer=mfr, tenant=self.tenant)
        # ?switch_tenant makes the request's tenant deterministic (member's only tenant).
        self.url = reverse('api:software_api:software-list') + f'?switch_tenant={self.tenant.pk}'
        self.client.force_login(self.user)

    @override_settings(ITAMBOX_PAGINATOR_COUNT_CAP=2)
    def test_count_capped_when_total_exceeds_cap(self):
        data = self.client.get(self.url).json()
        self.assertEqual(data['count'], 2)            # capped, not 3
        self.assertTrue(data['count_capped'])

    @override_settings(ITAMBOX_PAGINATOR_COUNT_CAP=100000)
    def test_count_exact_at_or_below_cap(self):
        data = self.client.get(self.url).json()
        self.assertEqual(data['count'], 3)            # exact
        self.assertFalse(data['count_capped'])

    @override_settings(ITAMBOX_PAGINATOR_COUNT_CAP=2)
    def test_cursor_mode_skips_count(self):
        data = self.client.get(self.url + '&start=0').json()
        self.assertIsNone(data['count'])              # no COUNT performed
        self.assertFalse(data['count_capped'])
