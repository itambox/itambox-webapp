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
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from django.urls import reverse

from core.models import FileAttachment
from assets.models import Asset
from compliance.models import CustodyReceipt
from organization.models import AssetHolder
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


class InitiateSignatureView(View):
    def get(self, request, token):
        receipt = get_object_or_404(CustodyReceipt, token=token)
        asset = receipt.asset
        holder = receipt.holder
        
        recipient_email = holder.email
        recipient_name = f"{holder.first_name} {holder.last_name}"
        
        if not recipient_email:
            return HttpResponse("Recipient email is not set on the asset holder.", status=400)
            
        is_onsite = request.GET.get('onsite') == 'true'
        
        # Check if active envelope already exists to prevent duplicate envelopes
        envelope = DocuSignEnvelope.objects.filter(
            asset=asset,
            recipient_email=recipient_email,
            status__in=['sent', 'delivered']
        ).first()
        
        if not envelope:
            try:
                # 1. Get Access Token
                access_token = get_docusign_access_token()
                config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
                account_id = config.get('DOCUSIGN_ACCOUNT_ID')
                base_url = config.get('DOCUSIGN_BASE_URL')
                
                # 2. Compile document
                document_text = f"""CUSTODY AGREEMENT AND RECEIPT
-----------------------------

Asset Name: {asset.name}
Asset Tag: {asset.asset_tag}
Serial Number: {asset.serial_number or 'N/A'}
Model: {asset.model or 'N/A'}

Recipient: {recipient_name} ({recipient_email})

{receipt.eula_text or ''}

{receipt.disclaimer or ''}

Signed: 
/sn1/

Date:
/date1/
"""
                document_base64 = base64.b64encode(document_text.encode('utf-8')).decode('utf-8')
                
                # 3. Create envelope call
                url = f"{base_url}/v2.1/accounts/{account_id}/envelopes"
                
                signer = {
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
                
                if is_onsite:
                    signer["clientUserId"] = receipt.token
                    
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
                        "signers": [signer]
                    },
                    "status": "sent"
                }
                
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
                
                response = requests.post(url, json=payload, headers=headers, timeout=15)
                response.raise_for_status()
                envelope_data = response.json()
                
                envelope = DocuSignEnvelope.objects.create(
                    asset=asset,
                    envelope_id=envelope_data['envelopeId'],
                    status='sent',
                    recipient_email=recipient_email,
                    recipient_name=recipient_name
                )
                
            except Exception as e:
                logger.error(f"Failed to initiate DocuSign signature for receipt {receipt.id}: {e}")
                return HttpResponse(f"DocuSign integration error: {e}", status=500)
        
        # If on-site signing is requested, generate Recipient View URL
        if is_onsite:
            try:
                access_token = get_docusign_access_token()
                config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
                account_id = config.get('DOCUSIGN_ACCOUNT_ID')
                base_url = config.get('DOCUSIGN_BASE_URL')
                
                view_url = f"{base_url}/v2.1/accounts/{account_id}/envelopes/{envelope.envelope_id}/views/recipient"
                
                view_payload = {
                    "returnUrl": request.build_absolute_uri(
                        reverse('plugins:itambox_esign:return_view', kwargs={'token': receipt.token})
                    ),
                    "authenticationMethod": "none",
                    "email": recipient_email,
                    "userName": recipient_name,
                    "clientUserId": receipt.token,
                    "recipientId": "1"
                }
                
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
                
                view_response = requests.post(view_url, json=view_payload, headers=headers, timeout=15)
                view_response.raise_for_status()
                view_data = view_response.json()
                
                return redirect(view_data['url'])
                
            except Exception as e:
                logger.error(f"Failed to generate DocuSign embedded view for receipt {receipt.id}: {e}")
                return HttpResponse(f"DocuSign embedded view error: {e}", status=500)
                
        return render(request, 'itambox_esign/sent_info.html', {'email': recipient_email})


class DocuSignReturnView(View):
    def get(self, request, token):
        receipt = get_object_or_404(CustodyReceipt, token=token)
        asset = receipt.asset
        holder = receipt.holder
        
        envelope = DocuSignEnvelope.objects.filter(
            asset=asset,
            recipient_email=holder.email,
            status='sent'
        ).first()
        
        if envelope:
            try:
                access_token = get_docusign_access_token()
                config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
                account_id = config.get('DOCUSIGN_ACCOUNT_ID')
                base_url = config.get('DOCUSIGN_BASE_URL')
                
                status_url = f"{base_url}/v2.1/accounts/{account_id}/envelopes/{envelope.envelope_id}"
                headers = {
                    "Authorization": f"Bearer {access_token}"
                }
                
                status_response = requests.get(status_url, headers=headers, timeout=15)
                status_response.raise_for_status()
                status_data = status_response.json()
                
                current_status = status_data.get('status')
                
                if current_status == 'completed':
                    doc_url = f"{base_url}/v2.1/accounts/{account_id}/envelopes/{envelope.envelope_id}/documents/combined"
                    doc_response = requests.get(doc_url, headers=headers, timeout=15)
                    if doc_response.status_code == 200:
                        asset_content_type = ContentType.objects.get_for_model(asset)
                        
                        attachment = FileAttachment(
                            model=asset_content_type,
                            object_id=asset.id,
                            name=f"Signed_Custody_Receipt_{envelope.envelope_id[:8]}.pdf",
                            mime_type="application/pdf"
                        )
                        attachment.file.save(attachment.name, ContentFile(doc_response.content), save=True)
                        
                        envelope.status = 'completed'
                        envelope.signed_document = attachment
                        envelope.completed_at = timezone.now()
                        envelope.save()
                        
                        receipt.accepted = True
                        receipt.accepted_date = timezone.now()
                        receipt.acceptance_method = 'docusign'
                        receipt.acceptance_status = CustodyReceipt.STATUS_ACCEPTED
                        receipt.signature_data = f"DocuSign Envelope ID: {envelope.envelope_id}"
                        receipt.signed_at = timezone.now()
                        receipt.signature_hash = envelope.envelope_id
                        receipt.verification_hash = envelope.envelope_id
                        receipt.save()
                        
                        messages.success(request, "Custody receipt signed successfully via DocuSign!")
                elif current_status == 'declined':
                    envelope.status = 'declined'
                    envelope.save()
                    
                    receipt.acceptance_status = CustodyReceipt.STATUS_DECLINED
                    receipt.save(update_fields=['acceptance_status', 'updated_at'])
                    
                    messages.error(request, "Custody transfer declined via DocuSign.")
                    
            except Exception as e:
                logger.error(f"Error checking DocuSign envelope return status for receipt {receipt.id}: {e}")
                
        return redirect(asset.get_absolute_url())


@method_decorator(csrf_exempt, name='dispatch')
class DocuSignWebhookView(View):
    def post(self, request):
        try:
            payload = json.loads(request.body)
            event = payload.get('event')
            
            if event == 'envelope-completed':
                data = payload.get('data', {})
                envelope_id = data.get('envelopeId')
                
                envelope = DocuSignEnvelope.objects.filter(envelope_id=envelope_id).first()
                if envelope and envelope.status != 'completed':
                    token = get_docusign_access_token()
                    config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
                    
                    account_id = config.get('DOCUSIGN_ACCOUNT_ID')
                    base_url = config.get('DOCUSIGN_BASE_URL')
                    
                    doc_url = f"{base_url}/v2.1/accounts/{account_id}/envelopes/{envelope_id}/documents/combined"
                    headers = {
                        "Authorization": f"Bearer {token}"
                    }
                    
                    doc_response = requests.get(doc_url, headers=headers, timeout=15)
                    if doc_response.status_code == 200:
                        asset_content_type = ContentType.objects.get_for_model(envelope.asset)
                        
                        attachment = FileAttachment(
                            model=asset_content_type,
                            object_id=envelope.asset.id,
                            name=f"Signed_Custody_Receipt_{envelope_id[:8]}.pdf",
                            mime_type="application/pdf"
                        )
                        attachment.file.save(attachment.name, ContentFile(doc_response.content), save=True)
                        
                        envelope.status = 'completed'
                        envelope.signed_document = attachment
                        envelope.completed_at = timezone.now()
                        envelope.save()
                        
                        # Find and update matching CustodyReceipt
                        holder = AssetHolder.objects.filter(email=envelope.recipient_email).first()
                        if holder:
                            receipt = CustodyReceipt.objects.filter(
                                asset=envelope.asset,
                                holder=holder,
                                acceptance_status='pending'
                            ).order_by('-created_date').first()
                            if receipt:
                                receipt.accepted = True
                                receipt.accepted_date = timezone.now()
                                receipt.acceptance_method = 'docusign'
                                receipt.acceptance_status = CustodyReceipt.STATUS_ACCEPTED
                                receipt.signature_data = f"DocuSign Envelope ID: {envelope_id}"
                                receipt.signed_at = timezone.now()
                                receipt.signature_hash = envelope_id
                                receipt.verification_hash = envelope_id
                                receipt.save()
                        
                        logger.info(f"Successfully processed webhook for signed envelope {envelope_id}.")
                        return HttpResponse("Webhook processed successfully.", status=200)
                        
            elif event == 'envelope-declined':
                data = payload.get('data', {})
                envelope_id = data.get('envelopeId')
                
                envelope = DocuSignEnvelope.objects.filter(envelope_id=envelope_id).first()
                if envelope and envelope.status != 'declined':
                    envelope.status = 'declined'
                    envelope.save()
                    
                    holder = AssetHolder.objects.filter(email=envelope.recipient_email).first()
                    if holder:
                        receipt = CustodyReceipt.objects.filter(
                            asset=envelope.asset,
                            holder=holder,
                            acceptance_status='pending'
                        ).order_by('-created_date').first()
                        if receipt:
                            receipt.acceptance_status = CustodyReceipt.STATUS_DECLINED
                            receipt.save(update_fields=['acceptance_status', 'updated_at'])
                            
                    logger.info(f"Successfully processed decline webhook for envelope {envelope_id}.")
                    return HttpResponse("Decline webhook processed successfully.", status=200)
            
            return HttpResponse("Event ignored.", status=200)
            
        except Exception as e:
            logger.error(f"Error handling DocuSign webhook callback: {e}")
            return HttpResponse(f"Error processing webhook: {e}", status=500)
