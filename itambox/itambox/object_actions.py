from dataclasses import dataclass
from typing import Callable, Optional

from django.utils.translation import gettext_lazy as _


@dataclass
class ObjectAction:
    name: str
    label: str
    icon: str = ''
    permissions_required: tuple = ()
    color: str = 'primary'


AddObject = ObjectAction('add', _('Add'), icon='plus', permissions_required=('add',))
EditObject = ObjectAction('edit', _('Edit'), icon='pencil', permissions_required=('change',))
DeleteObject = ObjectAction('delete', _('Delete'), icon='trash', permissions_required=('delete',), color='danger')
BulkEdit = ObjectAction('bulk_edit', _('Bulk Edit'), permissions_required=('change',))
BulkDelete = ObjectAction('bulk_delete', _('Bulk Delete'), permissions_required=('delete',), color='danger')
ImportObjects = ObjectAction('import', _('Import'), icon='upload', permissions_required=('add',))
ExportObjects = ObjectAction('export', _('Export'), icon='download', permissions_required=('view',))
CloneObject = ObjectAction('clone', _('Clone'), icon='mdi-content-copy',
                           permissions_required=('add',))
CheckoutObject = ObjectAction('checkout', _('Checkout'),
                              icon='mdi-arrow-right-bold-box',
                              permissions_required=('change',))
CheckinObject = ObjectAction('checkin', _('Checkin'),
                             icon='mdi-arrow-left-bold-box',
                             permissions_required=('change',))
AuditObject = ObjectAction('audit', _('Audit'), icon='mdi-clipboard-check',
                           permissions_required=('change',))
PrintLabelObject = ObjectAction('label', _('Print Label'), icon='mdi-printer',
                                permissions_required=('view',))
