# itambox/core/forms/__init__.py
#
# Re-exports all framework-level form classes from core.forms.mixins.
# Domain forms that depend on extras (Webhook, EventRule, Label, Report,
# ScheduledReport, AlertRule, NotificationChannel, ObjectChangeFilter) have
# moved to extras/forms.py.  This module no longer imports extras at all,
# breaking the eager extras dependency.

from .mixins import (
    OBJ_TYPE_CHOICES,
    SearchForm,
    JournalEntryForm,
    ConfirmationForm,
    BULK_EDIT_FIELD_BLACKLIST,
    BULK_EDIT_FIELD_TYPE_MAP,
    BulkEditForm,
    CrispyFormMixin,
    SlugModelForm,
    FilterForm,
    ColorFieldFormMixin,
)
from .tenant import scope_tenant_field

__all__ = [
    # mixins / base forms
    "OBJ_TYPE_CHOICES",
    "SearchForm",
    "JournalEntryForm",
    "ConfirmationForm",
    "BULK_EDIT_FIELD_BLACKLIST",
    "BULK_EDIT_FIELD_TYPE_MAP",
    "BulkEditForm",
    "CrispyFormMixin",
    "SlugModelForm",
    "FilterForm",
    "ColorFieldFormMixin",
    "scope_tenant_field",
]
