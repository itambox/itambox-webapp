import base64
import json
import logging
import requests
from django.conf import settings
from itambox.views.htmx import BaseHTMXView
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from core.models import FileAttachment
from assets.models import Asset
from .models import DocuSignEnvelope
from .auth import get_docusign_access_token

logger = logging.getLogger(__name__)

class DocuSignDashboardView(LoginRequiredMixin, BaseHTMXView, TemplateView):
    template_name = 'itambox_esign/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'DocuSign Dashboard'
        context['breadcrumbs'] = (
            ('/', 'Home'),
            ('', 'DocuSign Integration'),
        )
        
        envelopes = DocuSignEnvelope.objects.all()
        context['envelopes'] = envelopes
        context['total_count'] = envelopes.count()
        context['pending_count'] = envelopes.filter(status__in=['sent', 'delivered']).count()
        context['completed_count'] = envelopes.filter(status='completed').count()
        context['declined_count'] = envelopes.filter(status='declined').count()
        return context


class SendEnvelopeView(LoginRequiredMixin, View):
    def post(self, request, asset_id):
        asset = get_object_or_404(Asset, pk=asset_id)
        assignment = asset.assignments.filter(is_active=True).first()
        
        if not assignment or not assignment.assigned_user:
            messages.error(request, "Asset must be actively assigned to a user to request signatures.")
            return redirect(asset.get_absolute_url())

        recipient_email = assignment.assigned_user.email
        recipient_name = str(assignment.assigned_user)

        # 1. Compile the Custody Text Document
        document_text = f"""CUSTODY AGREEMENT AND RECEIPT
-----------------------------

Asset Name: {asset.name}
Asset Tag: {asset.asset_tag}
Serial Number: {asset.serial_number or 'N/A'}
Model: {asset.model or 'N/A'}

Recipient: {recipient_name} ({recipient_email})
Date Issued: {assignment.checked_out_at.strftime('%Y-%m-%d')}

I hereby acknowledge receipt of the asset listed above. I agree to keep it in good condition and return it to the company upon termination of my employment or request.

Signed: 
/sn1/

Date:
/date1/
"""
        document_base64 = base64.b64encode(document_text.encode('utf-8')).decode('utf-8')

        try:
            # 2. Get Access Token
            token = get_docusign_access_token()
            config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
            
            account_id = config.get('DOCUSIGN_ACCOUNT_ID')
            base_url = config.get('DOCUSIGN_BASE_URL')
            
            # 3. Create envelope call
            url = f"{base_url}/v2.1/accounts/{account_id}/envelopes"
            
            payload = {
                "emailSubject": f"ITAMbox: Receipt and Custody Agreement for {asset.name}",
                "documents": [
                    {
                        "documentBase64": document_base64,
                        "name": "CustodyAgreement.txt",
                        "fileExtension": "txt",
                        "documentId": "1"
                    }
                ],
                "recipients": {
                    "signers": [
                        {
                            "email": recipient_email,
                            "name": recipient_name,
                            "recipientId": "1",
                            "routingOrder": "1",
                            "tabs": {
                                "signHereTabs": [
                                    {
                                        "anchorString": "/sn1/",
                                        "anchorUnits": "pixels",
                                        "anchorXOffset": "20",
                                        "anchorYOffset": "10"
                                    }
                                ],
                                "dateSignedTabs": [
                                    {
                                        "anchorString": "/date1/",
                                        "anchorUnits": "pixels",
                                        "anchorXOffset": "20",
                                        "anchorYOffset": "10"
                                    }
                                ]
                            }
                        }
                    ]
                },
                "status": "sent"
            }

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            
            envelope_data = response.json()
            
            # 4. Save tracking record
            DocuSignEnvelope.objects.create(
                asset=asset,
                envelope_id=envelope_data['envelopeId'],
                status='sent',
                recipient_email=recipient_email,
                recipient_name=recipient_name
            )
            
            messages.success(request, f"Signature request sent to {recipient_name} via DocuSign!")
            
        except Exception as e:
            logger.error(f"Failed to create DocuSign envelope for asset {asset.id}: {e}")
            messages.error(request, f"DocuSign integration error: {e}")
            
        return redirect(asset.get_absolute_url())


@method_decorator(csrf_exempt, name='dispatch')
class DocuSignWebhookView(View):
    def post(self, request):
        try:
            payload = json.loads(request.body)
            # DocuSign Connect event structure check
            event = payload.get('event')
            
            # Handle standard envelope-completed webhook
            if event == 'envelope-completed':
                data = payload.get('data', {})
                envelope_id = data.get('envelopeId')
                
                envelope = DocuSignEnvelope.objects.filter(envelope_id=envelope_id).first()
                if envelope and envelope.status != 'completed':
                    # Download completed/signed document from DocuSign
                    token = get_docusign_access_token()
                    config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
                    
                    account_id = config.get('DOCUSIGN_ACCOUNT_ID')
                    base_url = config.get('DOCUSIGN_BASE_URL')
                    
                    # Fetch PDF of completed envelope documents
                    doc_url = f"{base_url}/v2.1/accounts/{account_id}/envelopes/{envelope_id}/documents/combined"
                    headers = {
                        "Authorization": f"Bearer {token}"
                    }
                    
                    doc_response = requests.get(doc_url, headers=headers, timeout=15)
                    if doc_response.status_code == 200:
                        # Save PDF as FileAttachment on Asset
                        asset_content_type = ContentType.objects.get_for_model(envelope.asset)
                        
                        attachment = FileAttachment(
                            model=asset_content_type,
                            object_id=envelope.asset.id,
                            name=f"Signed_Custody_Receipt_{envelope_id[:8]}.pdf",
                            mime_type="application/pdf"
                        )
                        attachment.file.save(attachment.name, ContentFile(doc_response.content), save=True)
                        
                        # Update database log status
                        envelope.status = 'completed'
                        envelope.signed_document = attachment
                        envelope.completed_at = timezone.now()
                        envelope.save()
                        
                        logger.info(f"Successfully processed webhook for signed envelope {envelope_id}.")
                        return HttpResponse("Webhook processed successfully.", status=200)
                    else:
                        logger.error(f"Failed to fetch completed document from DocuSign for envelope {envelope_id}.")
            
            # Return success to webhook caller for any unhandled events
            return HttpResponse("Event ignored.", status=200)
            
        except Exception as e:
            logger.error(f"Error handling DocuSign webhook callback: {e}")
            return HttpResponse(f"Error processing webhook: {e}", status=500)
