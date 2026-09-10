"""Structured, transport-neutral errors for type-library validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

PathPart: TypeAlias = str | int


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One deterministic validation failure."""

    code: str
    path: tuple[PathPart, ...]
    message: str

    def __str__(self) -> str:
        rendered_path = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in self.path).lstrip(".")
        return f"{self.code} at {rendered_path or '<document>'}: {self.message}"


class LibraryValidationError(ValueError):
    """Raised when a JSON library document is not a valid normalized graph."""

    def __init__(self, issues: ValidationIssue | tuple[ValidationIssue, ...] | list[ValidationIssue]):
        if isinstance(issues, ValidationIssue):
            normalized = (issues,)
        else:
            normalized = tuple(issues)
        if not normalized:
            raise ValueError("LibraryValidationError requires at least one issue")
        self.issues = normalized
        self.code = normalized[0].code
        self.path = normalized[0].path
        super().__init__("; ".join(str(issue) for issue in normalized))


def issue(code: str, path: tuple[PathPart, ...], message: str) -> LibraryValidationError:
    """Build a validation error without exposing framework-specific exceptions."""

    return LibraryValidationError(ValidationIssue(code=code, path=path, message=message))
