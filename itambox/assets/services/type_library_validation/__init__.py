"""Pure validation and canonicalization seam for Type Library v1 documents."""

from .errors import LibraryValidationError, ValidationIssue
from .limits import ValidationLimits
from .parser import parse_json_document
from .validation import (
    DependencyReference,
    InstalledDependency,
    ValidatedLibraryDocument,
    normalize_library_document,
    validate_library_document,
)

__all__ = [
    "DependencyReference",
    "InstalledDependency",
    "LibraryValidationError",
    "ValidatedLibraryDocument",
    "ValidationIssue",
    "ValidationLimits",
    "normalize_library_document",
    "parse_json_document",
    "validate_library_document",
]
