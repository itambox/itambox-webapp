from django.db import models
from django.utils import timezone
from itambox.plugins.models import PluginModel
from assets.models import Asset
from core.models import FileAttachment

class DocuSignEnvelope(PluginModel):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='docusign_envelopes'
    )
    envelope_id = models.CharField(max_length=100, unique=True, db_index=True)
    status = models.CharField(max_length=50, default='sent')  # sent, delivered, completed, declined
    recipient_email = models.EmailField()
    recipient_name = models.CharField(max_length=255)
    sent_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    signed_document = models.ForeignKey(
        FileAttachment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='esign_envelopes'
    )

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"Envelope {self.envelope_id} ({self.status}) for {self.asset}"
