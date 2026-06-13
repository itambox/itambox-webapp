import django_tables2 as tables
from django.db import models
from django.urls import reverse, NoReverseMatch
from django.utils.safestring import mark_safe
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from core.tables import ActionsColumn

# ============================================================
#  Model Mixin: CheckableInventoryModelMixin
# ============================================================

class CheckableInventoryModelMixin(models.Model):
    """
    Mixin for inventory models (Component, Accessory, Consumable) to support unified
    checking-out workflows, active assignment resolution, and absolute action URL lookups.
    """
    class Meta:
        abstract = True

    @property
    def checkout_url(self) -> str:
        """
        Dynamically returns the checkout URL name/path based on the model's namespace
        and view name convention (e.g. inventory:accessory_checkout).
        """
        app_label = self._meta.app_label
        model_name = self._meta.model_name
        try:
            return reverse(f"{app_label}:{model_name}_checkout", kwargs={'pk': self.pk})
        except NoReverseMatch:
            return ""

    @property
    def active_assignments(self) -> models.QuerySet:
        """
        Resolves the active assignment/allocation/consumption queryset for this model instance
        regardless of the varying reverse relationship names (assignments vs allocations vs consumptions).
        """
        if hasattr(self, 'assignments'):
            return self.assignments.all()
        elif hasattr(self, 'allocations'):
            # Soft-deletable component allocations
            return self.allocations.filter(deleted_at__isnull=True)
        elif hasattr(self, 'consumptions'):
            return self.consumptions.all()
        return None

    @property
    def active_assignments_count(self) -> int:
        """
        Returns the count of active assignments, allocations, or consumptions.
        """
        qs = self.active_assignments
        return qs.count() if qs is not None else 0


# ============================================================
#  Table Mixin: CheckableInventoryTableMixin
# ============================================================

class CheckoutActionsColumn(ActionsColumn):
    """Actions column for checkable inventory tables: prepends a per-row
    Check-out button to the standard clone/edit/delete actions, so no separate
    checkout column is needed. Uses the wider sticky variant to fit the button."""

    attrs = {
        'th': {'class': 'col-actions-wide text-nowrap'},
        'td': {'class': 'text-end text-nowrap noprint p-1 col-actions-wide'},
    }

    def get_leading_buttons(self, record, table):
        if getattr(record, 'deleted_at', None) is not None:
            return ''
        request = getattr(table, 'request', None)
        if not request:
            return ''

        app_label = record._meta.app_label
        model_name = record._meta.model_name
        if not table.has_perm(request.user, f"{app_label}.change_{model_name}", record):
            return ''

        url = getattr(record, 'checkout_url', '')
        if not url:
            try:
                url = reverse(f"{app_label}:{model_name}_checkout", kwargs={'pk': record.pk})
            except NoReverseMatch:
                return ''

        title = _('Check-out')
        return format_html(
            '<a class="btn btn-sm btn-soft-success check-action me-1" role="button" style="cursor: pointer" '
            'hx-get="{url}" hx-target="#modal-placeholder" hx-swap="innerHTML" '
            'title="{title}" aria-label="{title}"><i class="mdi mdi-logout me-1"></i>{title}</a>',
            url=url, title=title,
        )


class CheckableInventoryTableMixin(tables.Table):
    """
    Mixin for django_tables2 tables (ComponentTable, AccessoryTable, ConsumableTable):
    exposes a permission-aware "Check-out" button inside the actions column (no
    separate checkout column).
    """
    actions = CheckoutActionsColumn()
