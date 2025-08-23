from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ObjectAction:
    name: str
    label: str
    icon: str = ''
    permissions_required: tuple = ()
    color: str = 'primary'


AddObject = ObjectAction('add', 'Add', icon='plus', permissions_required=('add',))
EditObject = ObjectAction('edit', 'Edit', icon='pencil', permissions_required=('change',))
DeleteObject = ObjectAction('delete', 'Delete', icon='trash', permissions_required=('delete',), color='danger')
BulkEdit = ObjectAction('bulk_edit', 'Bulk Edit', permissions_required=('change',))
BulkDelete = ObjectAction('bulk_delete', 'Bulk Delete', permissions_required=('delete',), color='danger')
ImportObjects = ObjectAction('import', 'Import', icon='upload', permissions_required=('add',))
ExportObjects = ObjectAction('export', 'Export', icon='download', permissions_required=('view',))
