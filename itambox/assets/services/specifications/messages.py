"""User-facing text for specification command issues."""

from __future__ import annotations

from django.utils.translation import gettext as _

_MESSAGE_TEXT = {
    "specifications.invalid_type": "The submitted value has an invalid type.",
    "specifications.invalid_decimal": "Enter a decimal value with no more than three decimal places.",
    "specifications.invalid_range": "The submitted value is outside the allowed range.",
    "specifications.invalid_date": "Enter a valid date.",
    "specifications.invalid_choice": "Select a valid choice.",
    "specifications.required_field": "This field is required.",
    "specifications.unknown_field_key": "The field key is not part of this specification.",
    "specifications.read_only_field": "This field is read-only.",
    "specifications.conflict_clear_overlap": "A field cannot be set and cleared in the same patch.",
    "specifications.duplicate_field": "The field occurs more than once.",
    "specifications.immutable_definition": "The definition is immutable.",
    "specifications.ownership_conflict": "The submitted object is not owned by this scope.",
    "specifications.reference_conflict": "A referenced object is not available.",
    "specifications.dependency_retirement": "A referenced dependency cannot be retired.",
    "specifications.unsupported_structure": "The specification structure cannot be resolved.",
    "specifications.stale_resource": "The resource changed after the plan was created.",
    "specifications.stale_definition": "The effective definition changed after the plan was created.",
    "specifications.stale_plan": "The preview plan is no longer valid.",
    "specifications.export_blocked": "The requested export is blocked.",
    "specifications.missing_precondition": "A required write precondition is missing.",
}


def specification_message(message_key: str) -> str:
    """Render a command issue without exposing its internal ``specifications.*`` key."""
    if message_key == "specifications.object_unavailable":
        return _("You cannot perform this operation. Check your role permissions in the active provider tenant.")
    if message_key in _MESSAGE_TEXT:
        return _MESSAGE_TEXT[message_key]
    return message_key.rsplit(".", 1)[-1].replace("_", " ").capitalize()
