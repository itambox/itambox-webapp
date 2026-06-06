import django_tables2 as tables
from django.db import models
from django.urls import reverse, NoReverseMatch
from django.utils.safestring import mark_safe
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

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

class CheckableInventoryTableMixin(tables.Table):
    """
    Mixin for django_tables2 tables (ComponentTable, AccessoryTable, ConsumableTable)
    to render a permission-aware "Check-out" button/column inline for catalog lists.
    """
    checkout_checkin = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-checkout text-nowrap'},
            'td': {'class': 'text-center text-nowrap noprint p-1 col-checkout'}
        },
    )

    def render_checkout_checkin(self, record) -> str:
        request = getattr(self, 'request', None)
        if not request:
            return ""

        app_label = record._meta.app_label
        model_name = record._meta.model_name

        # Check if user has permission to checkout/change the inventory item
        permission_codename = f"{app_label}.change_{model_name}"
        if not self.has_perm(request.user, permission_codename, record):
            return mark_safe('<span class="text-muted small">—</span>')

        # Retrieve the dynamic checkout URL
        url = getattr(record, 'checkout_url', '')
        if not url:
            try:
                url = reverse(f"{app_label}:{model_name}_checkout", kwargs={'pk': record.pk})
            except NoReverseMatch:
                return mark_safe('<span class="text-muted small">—</span>')

        # Return the premium, HTMX-driven checkout modal launcher
        return format_html(
            '<div class="d-inline-block">'
            '  <a class="btn btn-sm btn-primary" hx-get="{}" hx-target="#modal-placeholder" hx-swap="innerHTML" href="javascript:void(0)">'
            '    <i class="mdi mdi-keyboard-tab-reverse"></i> Check-out'
            '  </a>'
            '</div>',
            url
        )
