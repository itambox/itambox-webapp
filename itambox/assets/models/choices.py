"""Lifecycle choice sets that live inside the models package.

These are defined here (not re-exported from assets.choices) because they are
tightly coupled to specific model fields and are only imported by sibling
submodules. assets.choices holds the app-wide vocabulary (StatusTypeChoices,
RequestStatusChoices) that views/forms/filters also consume.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _


class DisposalMethodChoices(models.TextChoices):
    RESALE = 'resale', _('Resale')
    RECYCLE = 'recycle', _('Recycle / WEEE')
    DONATION = 'donation', _('Donation')
    DESTRUCTION = 'destruction', _('Physical Destruction')
    RETURN_TO_LESSOR = 'return_to_lessor', _('Return to Lessor')
    OTHER = 'other', _('Other')


class DataSanitizationMethodChoices(models.TextChoices):
    """NIST SP 800-88 Rev. 1 aligned sanitization methods."""
    NONE = 'none', _('None / Not Applicable')
    NIST_CLEAR = 'nist_clear', _('NIST Clear (overwrite)')
    NIST_PURGE = 'nist_purge', _('NIST Purge (cryptographic or ATA Secure Erase)')
    NIST_DESTROY = 'nist_destroy', _('NIST Destroy (media destruction)')
    DOD_3PASS = 'dod_3pass', _('DoD 5220.22-M 3-Pass')
    DEGAUSS = 'degauss', _('Degaussing')
    PHYSICAL_DESTRUCTION = 'physical_destruction', _('Physical Destruction (shred/crush)')
    CRYPTO_ERASE = 'crypto_erase', _('Cryptographic Erase')


class WarrantyTypeChoices(models.TextChoices):
    HARDWARE = 'hardware', _('Hardware')
    PARTS_LABOR = 'parts_labor', _('Parts & Labor')
    ONSITE = 'onsite', _('On-site')
    ACCIDENTAL = 'accidental', _('Accidental Damage')
    EXTENDED = 'extended', _('Extended')
    FULL = 'full', _('Full Coverage')


class ReservationStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending')
    ACTIVE = 'active', _('Active')
    FULFILLED = 'fulfilled', _('Fulfilled')
    CANCELLED = 'cancelled', _('Cancelled')


class MaintenanceStatusChoices(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    IN_PROGRESS = 'in_progress', 'In Progress'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'
