import django_tables2 as tables
from core.tables import BaseTable, ActionsColumn
from .models import PurchaseOrder, Contract

class PurchaseOrderTable(BaseTable):
    order_number = tables.Column(linkify=True)
    supplier = tables.Column(linkify=True)
    status = tables.Column()
    expected_delivery_date = tables.Column()
    destination_location = tables.Column(linkify=True)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = PurchaseOrder
        fields = ('order_number', 'supplier', 'status', 'expected_delivery_date', 'destination_location', 'actions')


class ContractTable(BaseTable):
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
        fields = ('name', 'contract_number', 'contract_type', 'status', 'supplier', 'start_date', 'end_date', 'actions')
