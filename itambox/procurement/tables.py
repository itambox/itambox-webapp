import django_tables2 as tables
from core.tables import BaseTable, ActionsColumn, ToggleColumn
from .models import PurchaseOrder, Contract

class PurchaseOrderTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    order_number = tables.Column(linkify=True)
    supplier = tables.Column(linkify=True)
    status = tables.Column()
    expected_delivery_date = tables.Column()
    destination_location = tables.Column(linkify=True)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = PurchaseOrder
        fields = ('pk', 'order_number', 'supplier', 'status', 'expected_delivery_date', 'destination_location', 'actions')


class ContractTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.Column(linkify=True)
    contract_number = tables.Column(linkify=True)
    contract_type = tables.Column(verbose_name='Type')
    status = tables.Column()
    supplier = tables.Column(linkify=True)
    start_date = tables.Column()
    end_date = tables.Column()
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Contract
        fields = ('pk', 'name', 'contract_number', 'contract_type', 'status', 'supplier', 'start_date', 'end_date', 'actions')
