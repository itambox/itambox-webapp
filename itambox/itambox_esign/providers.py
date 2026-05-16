from compliance.providers import BaseSignatureProvider
from django.urls import reverse

class DocuSignSignatureProvider(BaseSignatureProvider):
    name = 'docusign'
    verbose_name = 'DocuSign Integration'

    def initiate_signature(self, receipt, request=None):
        if request:
            return request.build_absolute_uri(
                reverse('plugins:itambox_esign:initiate_signature', kwargs={'token': receipt.token})
            )
        return reverse('plugins:itambox_esign:initiate_signature', kwargs={'token': receipt.token})

    def verify_signature(self, payload):
        return True
