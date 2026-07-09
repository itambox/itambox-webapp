import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django_tables2.utils import A
from core.tables import ActionsColumn, BaseTable, CountLinkColumn, ToggleColumn
from extras.tables import TagColumn
from .models import Provider, Subscription, SubscriptionAssignment, SubscriptionStatusChoices


class ProviderTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('subscriptions:provider_detail', args=[A('pk')], verbose_name=_('Name'))
    is_active = tables.BooleanColumn(verbose_name=_('Active'), yesno='✓,✗')
    contact_email = tables.Column(accessor='primary_contact.email', verbose_name=_('Contact Email'))
    subscription_count = CountLinkColumn('subscriptions:subscription_list', 'provider', accessor='subscription_count', verbose_name=_('Subscriptions'), orderable=False)
    tags = TagColumn(url_name='subscriptions:provider_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Provider
        fields = ('pk', 'name', 'is_active', 'account_id', 'contact_email', 'subscription_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'is_active', 'account_id', 'subscription_count', 'tags', 'actions')


class SubscriptionTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('subscriptions:subscription_detail', args=[A('pk')], verbose_name=_('Name'))
    provider = tables.Column(linkify=True, verbose_name=_('Provider'))
    status = tables.Column(verbose_name=_('Status'))
    type = tables.Column(verbose_name=_('Type'))
    tenant = tables.Column(accessor='tenant.name', verbose_name=_('Tenant'), orderable=True)
    start_date = tables.DateColumn(format="Y-m-d", verbose_name=_('Start'))
    renewal_date = tables.DateColumn(format="Y-m-d", verbose_name=_('Next Renewal'))
    renewal_cost = tables.Column(verbose_name=_('Renewal Cost'))
    currency = tables.Column(verbose_name=_('Currency'))
    auto_renewal = tables.BooleanColumn(verbose_name=_('Auto-Renew'), yesno='✓,')
    tags = TagColumn(url_name='subscriptions:subscription_list')

    days_until_renewal = tables.Column(accessor='days_until_renewal', verbose_name=_('Due In'), orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Subscription
        fields = (
            'pk', 'name', 'provider', 'status', 'type', 'tenant',
            'start_date', 'renewal_date', 'renewal_cost', 'currency',
            'auto_renewal', 'tags', 'days_until_renewal', 'actions',
        )
        default_columns = (
            'pk', 'name', 'provider', 'status', 'type',
            'renewal_date', 'renewal_cost', 'tags', 'days_until_renewal', 'actions',
        )

    def render_status(self, value, record):
        if record and record.status:
            from itambox.utils import get_status_color
            display = record.get_status_display()
            color = get_status_color(record.status)
            return format_html(
                '<span class="badge badge-status" style="--status-color: #{};">'
                '<span class="badge-status-dot"></span>{}</span>',
                color, display
            )
        return "—"

    def render_renewal_cost(self, value, record):
        if value is not None:
            return f"{value:,.2f} {record.currency or 'USD'}"
        return "—"

    def render_days_until_renewal(self, value):
        if value is None:
            return "—"
        if value < 0:
            return format_html(
                '<span class="text-danger fw-bold">{} days overdue</span>', abs(value)
            )
        elif value == 0:
            return format_html('<span class="text-warning fw-bold">Today</span>')
        elif value <= 30:
            return format_html('<span class="text-warning">{} days</span>', value)
        else:
            return f"{value} days"


class SubscriptionAssignmentTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    subscription = tables.LinkColumn(
        'subscriptions:subscription_detail', args=[A('subscription.pk')],
        accessor='subscription.name', verbose_name=_('Subscription')
    )
    assigned_object = tables.Column(verbose_name=_('Assigned To'), orderable=False)
    assigned_date = tables.DateColumn(format='Y-m-d H:i', verbose_name=_('Assigned'))
    assigned_by = tables.Column(accessor='assigned_by.username', verbose_name=_('By'), default='—')
    notes = tables.Column(verbose_name=_('Notes'))
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = SubscriptionAssignment
        fields = ('pk', 'subscription', 'assigned_object', 'assigned_date', 'assigned_by', 'notes', 'actions')
        default_columns = ('pk', 'subscription', 'assigned_object', 'assigned_date', 'assigned_by', 'actions')

    def render_assigned_object(self, value, record):
        obj = record.assigned_object
        if obj is None:
            return "—"
        return str(obj)
