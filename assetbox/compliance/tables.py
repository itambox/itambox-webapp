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
