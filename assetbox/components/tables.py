import django_tables2 as tables
from django_tables2.utils import A
from core.tables import ActionsColumn, BaseTable, ToggleColumn
from extras.tables import TagColumn
from .models import ComponentType, ComponentInstance

class ComponentTypeTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:componenttype_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    specs = tables.Column(verbose_name='Specifications')
    tags = TagColumn(url_name='assets:componenttype_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ComponentType
        fields = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'specs', 'tags', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'specs', 'tags', 'actions')


class ComponentInstanceTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    component_type = tables.LinkColumn('assets:componentinstance_detail', args=[A('pk')], verbose_name='Component')
    serial_number = tables.Column(verbose_name='Serial Number')
    parent_asset = tables.LinkColumn('assets:asset_detail', args=[A('parent_asset__pk')], verbose_name='Asset Installed In')
    status = tables.Column(verbose_name='Status')
    tags = TagColumn(url_name='assets:componentinstance_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ComponentInstance
        fields = ('pk', 'component_type', 'serial_number', 'parent_asset', 'status', 'purchase_date', 'purchase_cost', 'notes', 'tags', 'actions')
        default_columns = ('pk', 'component_type', 'serial_number', 'parent_asset', 'status', 'purchase_date', 'purchase_cost', 'notes', 'tags', 'actions')
