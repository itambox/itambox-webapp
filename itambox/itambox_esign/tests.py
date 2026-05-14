import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from core.models import FileAttachment
from assets.models import Asset, StatusLabel
from organization.models import Tenant, AssetHolder
from itambox_esign.models import DocuSignEnvelope

User = get_user_model()

class DocuSignPluginTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant")
        self.status, _ = StatusLabel.objects.get_or_create(
            name="Test Available Status",
            defaults={"slug": "test-available-status", "type": "deployable"}
        )
        
        self.user = User.objects.create_user(username="testadmin", password="password", is_superuser=True)
        self.holder = AssetHolder.objects.create(
            first_name="John",
            last_name="Doe",
            upn="john.doe@example.com",
            email="john.doe@example.com",
            tenant=self.tenant
        )
        
        self.asset = Asset.objects.create(
            name="Test Laptop",
            asset_tag="TEST-000001",
            status=self.status,
            tenant=self.tenant
        )

    def test_envelope_model_creation(self):
        envelope = DocuSignEnvelope.objects.create(
            asset=self.asset,
            envelope_id="test-env-id-12345",
            status="sent",
            recipient_email="john.doe@example.com",
            recipient_name="John Doe"
        )
        self.assertEqual(envelope.asset, self.asset)
        self.assertEqual(envelope.envelope_id, "test-env-id-12345")
        self.assertEqual(envelope.status, "sent")
        self.assertEqual(str(envelope), "Envelope test-env-id-12345 (sent) for Test Laptop (TEST-000001)")

    def test_send_envelope_fails_without_assignment(self):
        self.client.login(username="testadmin", password="password")
        url = reverse('plugins:itambox_esign:send_envelope', kwargs={'asset_id': self.asset.id})
        
        # Post request to send envelope
        response = self.client.post(url)
        # Should redirect back to asset detail page
        self.assertEqual(response.status_code, 302)
        
        # Check that no envelope was created since there is no active assignment
        self.assertEqual(DocuSignEnvelope.objects.filter(asset=self.asset).count(), 0)

    def test_webhook_completed_updates_status(self):
        envelope = DocuSignEnvelope.objects.create(
            asset=self.asset,
            envelope_id="test-webhook-env-id",
            status="sent",
            recipient_email="john.doe@example.com",
            recipient_name="John Doe"
        )

        # Mock DocuSign access token call and PDF document download
        from unittest.mock import patch
        
        class MockResponse:
            def __init__(self, content, status_code):
                self.content = content
                self.status_code = status_code

        # Patch token auth and requests.get for downloading file
        with patch('itambox_esign.views.get_docusign_access_token', return_value="mock_token"), \
             patch('requests.get', return_value=MockResponse(b"%PDF-1.4 mock pdf data", 200)):
            
            webhook_url = reverse('plugins:itambox_esign:webhook')
            
            # Post dummy envelope-completed connect payload
            payload = {
                "event": "envelope-completed",
                "data": {
                    "envelopeId": "test-webhook-env-id"
                }
            }
            
            response = self.client.post(
                webhook_url,
                data=json.dumps(payload),
                content_type="application/json"
            )
            
            self.assertEqual(response.status_code, 200)
            
            # Refresh envelope from db and verify status
            envelope.refresh_from_db()
            self.assertEqual(envelope.status, 'completed')
            self.assertIsNotNone(envelope.completed_at)
            self.assertIsNotNone(envelope.signed_document)
            self.assertEqual(envelope.signed_document.name, "Signed_Custody_Receipt_test-web.pdf")
            self.assertEqual(envelope.signed_document.file.read(), b"%PDF-1.4 mock pdf data")
