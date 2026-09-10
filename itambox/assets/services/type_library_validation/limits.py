"""Resource envelope for JSON type-library validation."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ValidationLimits:
    """Hard limits shared by both halves of a snapshot document."""

    max_bytes: int = 10 * 1024 * 1024
    max_depth: int = 32
    max_fields: int = 2_000
    max_fieldsets: int = 256
    max_choice_sets: int = 256
    max_choices_per_set: int = 256
    max_total_choices: int = 10_000
    max_asset_types: int = 1_000
    max_dependencies: int = 16
    max_sections_per_type: int = 32
    max_fields_per_section: int = 256
    max_effective_fields_per_type: int = 512
    max_specifications_per_type: int = 4_096
    max_historical_specifications_per_type: int = 4_096

    @classmethod
    def from_value(cls, value: "ValidationLimits | Mapping[str, int] | None") -> "ValidationLimits":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("limits must be ValidationLimits, a mapping, or None")
        names = {field.name for field in fields(cls)}
        unknown = set(value) - names
        if unknown:
            raise ValueError(f"Unknown validation limit(s): {', '.join(sorted(unknown))}")
        invalid = {name: bound for name, bound in value.items() if type(bound) is not int or bound < 0}
        if invalid:
            raise ValueError("Validation limits must be non-negative integers")
        return replace(cls(), **value)
