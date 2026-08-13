"""Compatibility exports for the canonical bulk-import service.

The implementation lives in ``core.importers.bulk_forms``. This module remains
source-compatible for existing form, navigation, and test imports while keeping
presentation code out of the worker-facing service.
"""

from core.importers.bulk_forms import (
    IMPORT_EXCLUDED_FIELDS,
    IMPORT_EXCLUDED_MODELS,
    MAX_IMPORT_ROWS,
    BulkImportForm,
    ImportResult,
    _import_log_extra,
    _model_has_concrete_field,
    get_import_form_class,
    get_registered_import_form,
    is_model_importable,
    register_import_form,
    resolve_related,
)

__all__ = [
    "IMPORT_EXCLUDED_FIELDS",
    "IMPORT_EXCLUDED_MODELS",
    "MAX_IMPORT_ROWS",
    "BulkImportForm",
    "ImportResult",
    "get_import_form_class",
    "get_registered_import_form",
    "is_model_importable",
    "register_import_form",
    "resolve_related",
]
