from django.urls import reverse
from django.utils.translation import gettext_lazy as _

class BaseSignatureProvider:
    name = None
    verbose_name = None

    def initiate_signature(self, receipt, request=None):
        raise NotImplementedError

    def verify_signature(self, payload):
        raise NotImplementedError


class LocalSignatureProvider(BaseSignatureProvider):
    name = 'local'
    verbose_name = _('Local Canvas Signature Pad')

    def initiate_signature(self, receipt, request=None):
        if request:
            return request.build_absolute_uri(
                reverse('compliance:custody_eula_sign', kwargs={'token': receipt.token})
            )
        return reverse('compliance:custody_eula_sign', kwargs={'token': receipt.token})

    def verify_signature(self, payload):
        return True
