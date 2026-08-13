# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.
#
# Pure re-export shim: all 42 consumer import sites continue to work unchanged
# via "from itambox.views.generic import ...".
#
# Dependency order (no cycles):
#   utils -> {authorization, htmx_responses, related_objects, table_context}
#         -> mixins -> {table_config, restore, import_, delete, edit, detail, list_, bulk}
# Ruff's import sorting would move the features re-export to the top and create
# a runtime circular import, so preserve this explicitly documented order.
# isort: off

from itambox.views.htmx import BaseHTMXView  # noqa: F401

from itambox.views.generic.utils import (  # noqa: F401
    resolve_view_model,
    safe_return_url,
)

from itambox.views.generic.authorization import (  # noqa: F401
    PermissionResolver,
    SecuredObjectActionMixin,
)

from itambox.views.generic.htmx_responses import (  # noqa: F401
    error_response,
    is_htmx_request,
    success_response,
    trigger_response,
)

from itambox.views.generic.related_objects import RelatedObjectProvider  # noqa: F401

from itambox.views.generic.table_context import TableContextBuilder  # noqa: F401

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

# isort: on

__all__ = [
    # htmx base
    "BaseHTMXView",
    # utils
    "safe_return_url",
    "resolve_view_model",
    # authorization
    "PermissionResolver",
    "SecuredObjectActionMixin",
    # htmx responses
    "trigger_response",
    "success_response",
    "error_response",
    "is_htmx_request",
    # context components
    "RelatedObjectProvider",
    "TableContextBuilder",
    # mixins
    "CachedObjectMixin",
    "ObjectPermissionRequiredMixin",
    "GetReturnURLMixin",
    "ActionsMixin",
    "TableMixin",
    "TenantScopingViewMixin",
    "BulkViewMixin",
    "HtmxActionMixin",
    # table config
    "table_config",
    # restore / purge
    "ObjectRestoreView",
    "ObjectPurgeView",
    "ObjectBulkRestoreView",
    "ObjectBulkPurgeView",
    # import
    "ObjectImportView",
    "GenericObjectImportView",
    # CRUD
    "ObjectDeleteView",
    "ObjectEditView",
    "ObjectCloneView",
    "ObjectDetailView",
    "ObjectListView",
    # bulk
    "ObjectBulkEditView",
    "ObjectBulkDeleteView",
]
