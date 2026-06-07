import django_tables2 as tables
from core.tables import BaseTable, ActionsColumn
from .models import PurchaseOrder

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
