from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from extras.models import WebhookEndpoint

User = get_user_model()


class WebhookEndpointSSRFTests(APITestCase):
    """WS3-1: the SSRF guard must run at the WRITE boundary, not only at dispatch.
    An internal/loopback/metadata URL must be rejected on create."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='wh_admin', password='pw', is_staff=True, is_superuser=True
        )
        self.client.force_authenticate(self.admin)
        self.url = reverse('api:extras_api:webhookendpoint-list')

    def test_rejects_internal_target_urls(self):
        for target in (
            'http://169.254.169.254/latest/meta-data/',  # cloud metadata
            'http://127.0.0.1:6379/',                    # loopback
            'http://localhost/admin',                    # resolves to loopback
        ):
            resp = self.client.post(
                self.url, {'name': f'wh-{target[:18]}', 'target_url': target}, format='json'
            )
            self.assertEqual(resp.status_code, 400, f'{target} -> {resp.data}')
        self.assertEqual(WebhookEndpoint.objects.count(), 0)

    def test_accepts_external_url(self):
        # Public IP literal (no DNS) keeps this deterministic offline.
        resp = self.client.post(
            self.url, {'name': 'wh-ok', 'target_url': 'http://8.8.8.8/hook'}, format='json'
        )
        self.assertEqual(resp.status_code, 201, resp.data)
