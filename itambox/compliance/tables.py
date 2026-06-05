import django_tables2 as tables
from django_tables2.utils import A
from core.tables import ActionsColumn, BaseTable, ToggleColumn
from .models import AssetMaintenance

class AssetMaintenanceTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset__pk')], accessor='asset', verbose_name='Asset')
    title = tables.LinkColumn('compliance:assetmaintenance_detail', args=[A('pk')], verbose_name='Title')
    maintenance_type = tables.Column(verbose_name='Type')
    status = tables.Column(verbose_name='Status')
    supplier = tables.Column(accessor='supplier__name', verbose_name='Supplier')
    cost = tables.Column(verbose_name='Cost')
    start_date = tables.DateColumn(format="Y-m-d", verbose_name='Start Date')
    completion_date = tables.DateColumn(format="Y-m-d", verbose_name='Completion Date')
    downtime_days = tables.Column(accessor='downtime_days', verbose_name='Downtime (Days)', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetMaintenance
        fields = ('pk', 'asset', 'title', 'maintenance_type', 'status', 'supplier', 'cost', 'start_date', 'completion_date', 'downtime_days', 'actions')
        default_columns = ('pk', 'asset', 'title', 'maintenance_type', 'status', 'supplier', 'cost', 'start_date', 'completion_date', 'downtime_days', 'actions')

    def render_maintenance_type(self, record):
        return record.get_maintenance_type_display()

    def render_status(self, record):
        return record.get_status_display()

    def render_cost(self, value):
        if value is not None:
            return f"${value:,.2f}"
        return "\u2014"

    def render_downtime_days(self, value):
        if value is not None:
            if value == 0:
                return "Same day"
            return f"{value} day{'s' if value != 1 else ''}"
        return "\u2014"

    def render_supplier(self, value):
        return value or "\u2014"


from .models import CustodyTemplate, CustodyReceipt

class CustodyTemplateTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('compliance:custodytemplate_detail', args=[A('pk')], verbose_name='Name')
    tenant = tables.Column(verbose_name='Tenant')
    tenant_group = tables.Column(verbose_name='Tenant Group')
    signature_provider = tables.Column(verbose_name='Provider')
    is_active = tables.BooleanColumn(verbose_name='Active')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CustodyTemplate
        fields = ('pk', 'name', 'tenant', 'tenant_group', 'signature_provider', 'is_active', 'actions')
        default_columns = ('pk', 'name', 'tenant', 'tenant_group', 'signature_provider', 'is_active', 'actions')


class CustodyReceiptTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset__pk')], accessor='asset', verbose_name='Asset')
    holder = tables.LinkColumn('organization:assetholder_detail', args=[A('holder__pk')], accessor='holder', verbose_name='Holder')
    custody_template = tables.LinkColumn('compliance:custodytemplate_detail', args=[A('custody_template_id')], accessor='custody_template.name', verbose_name='Template')
    acceptance_status = tables.Column(verbose_name='Status')
    signed_at = tables.DateTimeColumn(format="Y-m-d H:i", verbose_name='Signed At')
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-actions text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions'}
        },
    )

    class Meta(BaseTable.Meta):
        model = CustodyReceipt
        fields = ('pk', 'asset', 'holder', 'custody_template', 'acceptance_status', 'signed_at', 'actions')
        default_columns = ('pk', 'asset', 'holder', 'custody_template', 'acceptance_status', 'signed_at', 'actions')

    def render_actions(self, record):
        from django.urls import reverse
        from django.utils.html import format_html
        url = reverse('compliance:custody_eula_sign', kwargs={'token': record.token})
        return format_html(
            '<a class="btn btn-sm btn-primary" href="{}" target="_blank" title="View/Sign Receipt">'
            '<i class="mdi mdi-eye-outline me-1"></i>View'
            '</a>',
            url
        )

    def render_acceptance_status(self, value):
        badges = {
            'pending': 'bg-warning text-warning-invert',
            'accepted': 'bg-success text-success-invert',
            'declined': 'bg-danger text-danger-invert',
        }
        badge_class = badges.get(value, 'bg-secondary')
        from django.utils.html import format_html
        return format_html('<span class="badge {}">{}</span>', badge_class, value.title())

