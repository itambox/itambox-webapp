from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditSessionStatusChoices(models.TextChoices):
    PLANNED = 'planned', _('Planned')
    ACTIVE = 'active', _('Active')
    COMPLETED = 'completed', _('Completed')


class AuditVerificationMethodChoices(models.TextChoices):
    BARCODE = 'barcode', _('Barcode Scan')
    RFID = 'rfid', _('RFID Reader')
    MANUAL = 'manual', _('Manual Input')
    AUTO = 'auto', _('Agent API Handshake')
