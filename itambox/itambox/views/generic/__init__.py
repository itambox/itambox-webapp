# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.
#
# Pure re-export shim: all 42 consumer import sites continue to work unchanged
# via "from itambox.views.generic import ...".
#
# Dependency order (no cycles):
#   utils -> mixins -> {table_config, restore, import_, delete, edit, detail, list_, bulk}

from itambox.views.htmx import BaseHTMXView  # noqa: F401

from itambox.views.generic.utils import safe_return_url  # noqa: F401

from itambox.views.generic.mixins import (  # noqa: F401
    CachedObjectMixin,
    ObjectPermissionRequiredMixin,
    GetReturnURLMixin,
    ActionsMixin,
    TableMixin,
    TenantScopingViewMixin,
    BulkViewMixin,
)

from itambox.views.generic.table_config import table_config  # noqa: F401

from itambox.views.generic.restore import (  # noqa: F401
    HtmxActionMixin,
    ObjectRestoreView,
    ObjectPurgeView,
    ObjectBulkRestoreView,
    ObjectBulkPurgeView,
)

from itambox.views.generic.import_ import (  # noqa: F401
    ObjectImportView,
    GenericObjectImportView,
)

from itambox.views.generic.delete import ObjectDeleteView  # noqa: F401

from itambox.views.generic.edit import (  # noqa: F401
    ObjectEditView,
    ObjectCloneView,
)

from itambox.views.generic.detail import ObjectDetailView  # noqa: F401

from itambox.views.generic.list_ import ObjectListView  # noqa: F401

from itambox.views.generic.bulk import (  # noqa: F401
    ObjectBulkEditView,
    ObjectBulkDeleteView,
)

# ObjectExportView lives in itambox.views.features; import it last to avoid the
# circular-import that arises when features.py imports back into generic.
from itambox.views.features import ObjectExportView  # noqa: F401, E402

__all__ = [
    # htmx base
    'BaseHTMXView',
    # utils
    'safe_return_url',
    # mixins
    'CachedObjectMixin',
    'ObjectPermissionRequiredMixin',
    'GetReturnURLMixin',
    'ActionsMixin',
    'TableMixin',
    'TenantScopingViewMixin',
    'BulkViewMixin',
    'HtmxActionMixin',
    # table config
    'table_config',
    # restore / purge
    'ObjectRestoreView',
    'ObjectPurgeView',
    'ObjectBulkRestoreView',
    'ObjectBulkPurgeView',
    # import
    'ObjectImportView',
    'GenericObjectImportView',
    # CRUD
    'ObjectDeleteView',
    'ObjectEditView',
    'ObjectCloneView',
    'ObjectDetailView',
    'ObjectListView',
    # bulk
    'ObjectBulkEditView',
    'ObjectBulkDeleteView',
    # export (late import to avoid circular)
    'ObjectExportView',
]
