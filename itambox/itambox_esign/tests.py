import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from unittest.mock import patch

from core.models import FileAttachment
from assets.models import Asset, StatusLabel
from organization.models import Tenant, AssetHolder
from compliance.models import CustodyReceipt
from compliance.registry import signature_providers
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

    def test_signature_provider_registered(self):
        choices = signature_providers.choices()
        self.assertIn(('docusign', 'DocuSign Integration'), choices)

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

    def test_compliance_sign_portal_redirects_for_docusign(self):
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            signature_provider='docusign'
        )
        url = reverse('compliance:custody_eula_sign', kwargs={'token': receipt.token})
        
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        expected_url = reverse('plugins:itambox_esign:initiate_signature', kwargs={'token': receipt.token})
        self.assertIn(expected_url, response.url)

    def test_compliance_sign_portal_redirects_with_onsite_prop(self):
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            signature_provider='docusign'
        )
        url = reverse('compliance:custody_eula_sign', kwargs={'token': receipt.token}) + "?onsite=true"
        
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        expected_url = reverse('plugins:itambox_esign:initiate_signature', kwargs={'token': receipt.token}) + "?onsite=true"
        self.assertIn(expected_url, response.url)

    def test_initiate_signature_onsite_redirects_to_docusign(self):
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            signature_provider='docusign'
        )
        url = reverse('plugins:itambox_esign:initiate_signature', kwargs={'token': receipt.token}) + "?onsite=true"
        
        class MockResponse:
            def __init__(self, json_data, status_code):
                self.json_data = json_data
                self.status_code = status_code
            def json(self):
                return self.json_data
            def raise_for_status(self):
                pass

        with patch('itambox_esign.views.get_docusign_access_token', return_value="mock_token"), \
             patch('requests.post') as mock_post:
            
            # Mock the envelope creation and the recipient view URLs
            mock_post.side_effect = [
                MockResponse({"envelopeId": "mock-onsite-env-id"}, 201),
                MockResponse({"url": "https://demo.docusign.net/Signing/mock-signing-url"}, 201)
            ]
            
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, "https://demo.docusign.net/Signing/mock-signing-url")
            
            # Verify envelope created in DB
            self.assertTrue(DocuSignEnvelope.objects.filter(envelope_id="mock-onsite-env-id").exists())

    def test_initiate_signature_remote_renders_sent_info(self):
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            signature_provider='docusign'
        )
        url = reverse('plugins:itambox_esign:initiate_signature', kwargs={'token': receipt.token})
        
        class MockResponse:
            def __init__(self, json_data, status_code):
                self.json_data = json_data
                self.status_code = status_code
            def json(self):
                return self.json_data
            def raise_for_status(self):
                pass

        with patch('itambox_esign.views.get_docusign_access_token', return_value="mock_token"), \
             patch('requests.post', return_value=MockResponse({"envelopeId": "mock-remote-env-id"}, 201)):
            
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, 'itambox_esign/sent_info.html')
            self.assertContains(response, "john.doe@example.com")
            
            # Verify envelope created in DB
            self.assertTrue(DocuSignEnvelope.objects.filter(envelope_id="mock-remote-env-id").exists())

    def test_return_view_updates_status_on_completed(self):
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            signature_provider='docusign'
        )
        envelope = DocuSignEnvelope.objects.create(
            asset=self.asset,
            envelope_id="mock-return-env-id",
            status="sent",
            recipient_email="john.doe@example.com",
            recipient_name="John Doe"
        )
        url = reverse('plugins:itambox_esign:return_view', kwargs={'token': receipt.token})
        
        class MockResponse:
            def __init__(self, data, status_code, is_json=True):
                self.data = data
                self.status_code = status_code
                self.is_json = is_json
                if not is_json:
                    self.content = data
            def json(self):
                return self.data
            def raise_for_status(self):
                pass

        with patch('itambox_esign.views.get_docusign_access_token', return_value="mock_token"), \
             patch('requests.get') as mock_get:
            
            mock_get.side_effect = [
                MockResponse({"status": "completed"}, 200),
                MockResponse(b"%PDF-1.4 mock pdf return data", 200, is_json=False)
            ]
            
            response = self.client.get(url)
            # Should redirect to asset details page
            self.assertEqual(response.status_code, 302)
            
            envelope.refresh_from_db()
            receipt.refresh_from_db()
            
            self.assertEqual(envelope.status, 'completed')
            self.assertEqual(receipt.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)
            self.assertTrue(receipt.accepted)
            self.assertEqual(envelope.signed_document.file.read(), b"%PDF-1.4 mock pdf return data")

    def test_webhook_completed_updates_both_envelope_and_receipt(self):
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            signature_provider='docusign'
        )
        envelope = DocuSignEnvelope.objects.create(
            asset=self.asset,
            envelope_id="test-webhook-env-id",
            status="sent",
            recipient_email="john.doe@example.com",
            recipient_name="John Doe"
        )

        class MockResponse:
            def __init__(self, content, status_code):
                self.content = content
                self.status_code = status_code

        with patch('itambox_esign.views.get_docusign_access_token', return_value="mock_token"), \
             patch('requests.get', return_value=MockResponse(b"%PDF-1.4 mock pdf data", 200)):
            
            webhook_url = reverse('plugins:itambox_esign:webhook')
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
            
            envelope.refresh_from_db()
            receipt.refresh_from_db()
            
            self.assertEqual(envelope.status, 'completed')
            self.assertEqual(receipt.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)
            self.assertTrue(receipt.accepted)

    def test_webhook_declined_updates_both_envelope_and_receipt(self):
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            signature_provider='docusign'
        )
        envelope = DocuSignEnvelope.objects.create(
            asset=self.asset,
            envelope_id="test-webhook-decline-id",
            status="sent",
            recipient_email="john.doe@example.com",
            recipient_name="John Doe"
        )

        webhook_url = reverse('plugins:itambox_esign:webhook')
        payload = {
            "event": "envelope-declined",
            "data": {
                "envelopeId": "test-webhook-decline-id"
            }
        }
        
        response = self.client.post(
            webhook_url,
            data=json.dumps(payload),
            content_type="application/json"
        )
        
        self.assertEqual(response.status_code, 200)
        
        envelope.refresh_from_db()
        receipt.refresh_from_db()
        
        self.assertEqual(envelope.status, 'declined')
        self.assertEqual(receipt.acceptance_status, CustodyReceipt.STATUS_DECLINED)
        self.assertFalse(receipt.accepted)
