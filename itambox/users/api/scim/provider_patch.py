"""Pure SCIM PATCH parsing for provider user and group resources.

The parser deliberately knows nothing about Django models or request state. It turns
SCIM's loosely shaped JSON operations into immutable values that the provider mutation
services can apply inside their own transactions.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

UNSET = object()


class SCIMPatchError(ValueError):
    """A client-correctable SCIM PATCH validation error."""

    def __init__(self, detail: str, *, scim_type: str | None = None, status_code: int = 400):
        super().__init__(detail)
        self.scim_type = scim_type
        self.status_code = status_code


@dataclass(frozen=True)
class UserPatch:
    username: Any = UNSET
    email: Any = UNSET
    first_name: Any = UNSET
    last_name: Any = UNSET
    active: Any = UNSET
    external_id: Any = UNSET


@dataclass(frozen=True)
class GroupMemberOperation:
    op: str
    member_ids: tuple[int | str, ...] = ()
    filter_member_id: int | str | None = None
    clear_members: bool = False


@dataclass(frozen=True)
class GroupPatch:
    display_name: Any = UNSET
    member_operations: tuple[GroupMemberOperation, ...] = ()
    external_id: Any = UNSET


_MEMBER_FILTER_RE = re.compile(
    r"^\s*members\s*\[\s*value\s+eq\s+(?:\"([^\"]*)\"|'([^']*)'|([^\s\]]+))\s*\]\s*$",
    re.IGNORECASE,
)
_EMAIL_FILTER_RE = re.compile(
    r"^emails\s*\[\s*type\s+eq\s+(?:\"([^\"]+)\"|'([^']+)')\s*\]\s*\.value$",
    re.IGNORECASE,
)
MAX_PATCH_OPERATIONS = 1000
MAX_PATCH_MEMBERS = 10000
MAX_MEMBER_ID_DIGITS = 19
MAX_PATCH_PATH_LENGTH = 256
USER_NAME_MAX_LENGTH = 150
EMAIL_MAX_LENGTH = 254
NAME_FIELD_MAX_LENGTH = 150
EXTERNAL_ID_MAX_LENGTH = 255
DISPLAY_NAME_MAX_LENGTH = 100
_UNMANAGED_USER_PATHS = {
    "displayname",
    "usertype",
    "preferredlanguage",
    "locale",
    "timezone",
    "title",
    "profileurl",
    "costcenter",
    "department",
    "organization",
    "division",
    "manager",
    "name.formatted",
    "name.middlename",
    "name.honorificprefix",
    "name.honorificsuffix",
    "employeenumber",
    "nickname",
    "phonenumbers",
    "addresses",
    "photos",
    "roles",
    "groups",
    "entitlements",
    "x509certificates",
    "ims",
    "urn:ietf:params:scim:schemas:extension:enterprise:2.0:user",
}
_UNMANAGED_USER_PATH_PREFIXES = (
    "phonenumbers[",
    "addresses[",
    "photos[",
    "roles[",
    "groups[",
    "entitlements[",
    "x509certificates[",
    "ims[",
    "urn:ietf:params:scim:schemas:extension:enterprise:2.0:user:",
)


def _email_filter_type(path: str) -> str | None:
    match = _EMAIL_FILTER_RE.fullmatch(path)
    if match is None:
        return None
    return next(group for group in match.groups() if group is not None).strip().lower()


def _is_unmanaged_user_path(path: str) -> bool:
    normalized_path = path.lower()
    return normalized_path in _UNMANAGED_USER_PATHS or normalized_path.startswith(_UNMANAGED_USER_PATH_PREFIXES)


def _is_unmanaged_user_value(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    for key, nested_value in value.items():
        normalized_key = str(key).lower()
        if normalized_key == "name" and isinstance(nested_value, Mapping):
            if set(nested_value) - {
                "formatted",
                "middleName",
                "honorificPrefix",
                "honorificSuffix",
            }:
                return False
            continue
        if not _is_unmanaged_user_path(normalized_key):
            return False
    return True


def _is_unmanaged_user_target(path: str, value: Any) -> bool:
    email_type = _email_filter_type(path)
    if email_type is not None:
        return email_type != "work"
    if _is_unmanaged_user_path(path):
        return True
    return (
        path.lower() == "name"
        and isinstance(value, Mapping)
        and set(value)
        <= {
            "formatted",
            "middleName",
            "honorificPrefix",
            "honorificSuffix",
        }
    )


def _iter_operations(operations: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(operations, list):
        raise SCIMPatchError("Operations must be a list.")

    if not operations:
        raise SCIMPatchError("Operations must not be empty.")
    if len(operations) > MAX_PATCH_OPERATIONS:
        raise SCIMPatchError(f"Operations must not exceed {MAX_PATCH_OPERATIONS} items.")

    parsed = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise SCIMPatchError(f"Operation {index} must be an object.")
        parsed.append(operation)
    return parsed


def _coerce_member_id(value: Any) -> int | str:
    if isinstance(value, bool):
        raise SCIMPatchError("Invalid member ID")
    if isinstance(value, int):
        member_id = value
        if not 0 < member_id <= 2**63 - 1:
            raise SCIMPatchError("Invalid member ID")
        return member_id
    if isinstance(value, str):
        normalized = value.strip()
        if re.fullmatch(r"[0-9]+", normalized):
            if len(normalized) > MAX_MEMBER_ID_DIGITS:
                raise SCIMPatchError("Invalid member ID")
            member_id = int(normalized)
            if not 0 < member_id <= 2**63 - 1:
                raise SCIMPatchError("Invalid member ID")
            return member_id
        try:
            return str(UUID(normalized))
        except (TypeError, ValueError, AttributeError) as exc:
            raise SCIMPatchError("Invalid member ID") from exc
    raise SCIMPatchError("Invalid member ID")


def _member_values(value: Any) -> tuple[int | str, ...]:
    if isinstance(value, Mapping) and "members" in value:
        unknown_keys = set(value) - {"members", "displayName"}
        if unknown_keys:
            raise SCIMPatchError(f"Unsupported SCIM PATCH members keys: {sorted(unknown_keys)}")
        value = value["members"]
    if isinstance(value, Mapping):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise SCIMPatchError("Invalid member values")
    if len(values) > MAX_PATCH_MEMBERS:
        raise SCIMPatchError(f"Members must not exceed {MAX_PATCH_MEMBERS} items.")

    member_ids = []
    for item in values:
        if not isinstance(item, Mapping) or "value" not in item:
            raise SCIMPatchError("Invalid member ID")
        unknown_keys = set(item) - {"value", "display", "$ref", "type"}
        if unknown_keys:
            raise SCIMPatchError(f"Unsupported SCIM PATCH member keys: {sorted(unknown_keys)}")
        member_ids.append(_coerce_member_id(item["value"]))
    return tuple(member_ids)


def parse_member_ids(value: Any) -> tuple[int | str, ...]:
    """Validate a complete SCIM ``members`` array without touching the database."""
    if not isinstance(value, list):
        raise SCIMPatchError("members must be a list")
    return _member_values(value)


def _member_filter_id(path: str) -> int | None:
    match = _MEMBER_FILTER_RE.fullmatch(path)
    if match is None:
        if path.lower().startswith("members["):
            raise SCIMPatchError(f"Invalid member path: {path}")
        return None
    return _coerce_member_id(next(group for group in match.groups() if group is not None).strip())


def _operation_type(operation: Mapping[str, Any]) -> str:
    operation_value = operation.get("op", "")
    if not isinstance(operation_value, str):
        raise SCIMPatchError(f"Unsupported SCIM PATCH operation: {operation_value}")
    operation_type = operation_value.strip().lower()
    if operation_type not in {"add", "remove", "replace"}:
        raise SCIMPatchError(f"Unsupported SCIM PATCH operation: {operation_value}")
    return operation_type


def _operation_path(operation: Mapping[str, Any]) -> str:
    if "path" not in operation:
        return ""
    path = operation["path"]
    if not isinstance(path, str) or not path.strip():
        raise SCIMPatchError("path must be a non-empty string", scim_type="noTarget")
    if len(path) > MAX_PATCH_PATH_LENGTH:
        raise SCIMPatchError(f"path exceeds maximum length of {MAX_PATCH_PATH_LENGTH} characters")
    return path.strip()


def _operation_value(operation: Mapping[str, Any], operation_type: str) -> Any:
    if operation_type != "remove":
        if "value" not in operation or operation["value"] is None:
            raise SCIMPatchError(f"{operation_type} operation requires a non-null value")
    return operation.get("value")


def _active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise SCIMPatchError("active must be a boolean")


def _reject_control_characters(value: str, field_name: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SCIMPatchError(f"{field_name} contains control characters")


def _required_text(value: Any, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise SCIMPatchError(f"{field_name} must be a string")
    _reject_control_characters(value, field_name)
    if not value.strip():
        raise SCIMPatchError(f"{field_name} must not be empty")
    if len(value) > max_length:
        raise SCIMPatchError(f"{field_name} exceeds maximum length of {max_length} characters")
    return value


def _validate_email_item(item: Any) -> Mapping[str, Any]:
    if not isinstance(item, Mapping) or "value" not in item:
        raise SCIMPatchError("Email values must contain an object with value.")
    unknown_keys = set(item) - {"value", "type", "primary", "display", "$ref"}
    if unknown_keys:
        raise SCIMPatchError(f"Unsupported SCIM PATCH email keys: {sorted(unknown_keys)}")
    if "type" in item and not isinstance(item["type"], str):
        raise SCIMPatchError("Email type must be a string")
    if "primary" in item and not isinstance(item["primary"], bool):
        raise SCIMPatchError("Email primary must be a boolean")
    return item


def _email(value: Any) -> str:
    if value is None:
        raise SCIMPatchError("email must not be null")
    if isinstance(value, list):
        if not value:
            raise SCIMPatchError("Email values must not be empty")
        validated_items = [_validate_email_item(item) for item in value]
        selected = next((item for item in validated_items if item.get("primary") is True), validated_items[0])
        return _required_text(selected["value"], "email", EMAIL_MAX_LENGTH)
    if isinstance(value, Mapping):
        item = _validate_email_item(value)
        return _required_text(item["value"], "email", EMAIL_MAX_LENGTH)
    return _required_text(value, "email", EMAIL_MAX_LENGTH)


def _set_user_name_attributes(fields: dict[str, Any], value: Any) -> None:
    if not isinstance(value, Mapping):
        raise SCIMPatchError("name must be an object")
    if "givenName" in value:
        fields["first_name"] = _required_text(value["givenName"], "givenName", NAME_FIELD_MAX_LENGTH)
    if "familyName" in value:
        fields["last_name"] = _required_text(value["familyName"], "familyName", NAME_FIELD_MAX_LENGTH)
    unknown_keys = set(value) - {
        "givenName",
        "familyName",
        "formatted",
        "middleName",
        "honorificPrefix",
        "honorificSuffix",
    }
    if unknown_keys:
        raise SCIMPatchError(f"Unsupported SCIM PATCH name keys: {sorted(unknown_keys)}")


def _set_user_attribute(fields: dict[str, Any], name: Any, value: Any) -> None:
    normalized_name = str(name).lower()
    if normalized_name == "active":
        fields["active"] = _active(value)
    elif normalized_name == "username":
        fields["username"] = _required_text(value, "userName", USER_NAME_MAX_LENGTH)
    elif normalized_name == "emails":
        fields["email"] = _email(value)
    elif normalized_name == "externalid":
        fields["external_id"] = parse_external_id(value)
    elif normalized_name == "name":
        _set_user_name_attributes(fields, value)
    elif _is_unmanaged_user_path(normalized_name):
        return
    else:
        raise SCIMPatchError(f"Unsupported SCIM PATCH path: {name}")


def _parse_user_path(fields: dict[str, Any], path: str, value: Any) -> None:
    normalized_path = path.lower()
    if normalized_path == "active":
        fields["active"] = _active(value)
    elif normalized_path == "username":
        fields["username"] = _required_text(value, "userName", USER_NAME_MAX_LENGTH)
    elif normalized_path == "externalid":
        fields["external_id"] = parse_external_id(value)
    elif normalized_path in {"email", "emails", "emails.value"} or _email_filter_type(path) == "work":
        fields["email"] = _email(value)
    elif normalized_path == "name" and isinstance(value, Mapping):
        _set_user_attribute(fields, "name", value)
    elif normalized_path == "name.givenname":
        fields["first_name"] = _required_text(value, "givenName", NAME_FIELD_MAX_LENGTH)
    elif normalized_path == "name.familyname":
        fields["last_name"] = _required_text(value, "familyName", NAME_FIELD_MAX_LENGTH)
    elif _is_unmanaged_user_target(path, value):
        return
    else:
        target = path or "pathless value"
        raise SCIMPatchError(f"Unsupported SCIM PATCH path: {target}")


def _parse_user_remove(fields: dict[str, Any], path: str) -> None:
    if not path:
        raise SCIMPatchError("remove operation requires a path", scim_type="noTarget")
    normalized_path = path.lower()
    email_type = _email_filter_type(path)
    if normalized_path in {"email", "emails", "emails.value"} or email_type == "work":
        fields["email"] = ""
    elif normalized_path == "externalid":
        fields["external_id"] = ""
    elif normalized_path == "name.givenname":
        fields["first_name"] = ""
    elif normalized_path == "name.familyname":
        fields["last_name"] = ""
    elif email_type is not None or _is_unmanaged_user_path(path):
        return
    else:
        raise SCIMPatchError(f"Unsupported SCIM PATCH path: {path}")


def _parse_user_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
    operation_type = _operation_type(operation)
    path = _operation_path(operation)
    value = _operation_value(operation, operation_type)
    fields: dict[str, Any] = {}
    if operation_type == "remove":
        _parse_user_remove(fields, path)
    elif isinstance(value, Mapping) and not path:
        for name, nested_value in value.items():
            _set_user_attribute(fields, name, nested_value)
    else:
        _parse_user_path(fields, path, value)
    if not fields and not _is_unmanaged_user_target(path, value) and not _is_unmanaged_user_value(value):
        raise SCIMPatchError("PATCH operation did not target a supported attribute")
    return fields


def require_object_document(document: Any) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        raise SCIMPatchError("SCIM document must be an object")
    return document


def get_patch_operations(document: Any) -> Any:
    """Extract PATCH operations from an object-shaped SCIM document."""
    return require_object_document(document).get("Operations", [])


def parse_user_patch_operations(operations: Any) -> UserPatch:
    """Parse provider ``/Users/<id>`` PATCH operations without database access."""
    fields: dict[str, Any] = {}
    for operation in _iter_operations(operations):
        fields.update(_parse_user_operation(operation))
    return UserPatch(**fields)


def parse_user_resource(document: Any) -> UserPatch:
    """Parse a complete provider User resource for PUT without database access."""
    document = require_object_document(document)
    if "userName" not in document:
        raise SCIMPatchError("userName is required")

    username = _required_text(document["userName"], "userName", USER_NAME_MAX_LENGTH)
    emails = document.get("emails", [])
    email = "" if emails == [] else _email(emails)
    external_id = parse_external_id(document.get("externalId"))

    name = document.get("name", {})
    if not isinstance(name, Mapping):
        raise SCIMPatchError("name must be an object")
    unknown_name_keys = {
        key
        for key in name
        if key not in {"givenName", "familyName", "formatted", "middleName", "honorificPrefix", "honorificSuffix"}
    }
    if unknown_name_keys:
        raise SCIMPatchError(f"Unsupported SCIM name keys: {sorted(unknown_name_keys)}")
    first_name = (
        "" if "givenName" not in name else _required_text(name["givenName"], "givenName", NAME_FIELD_MAX_LENGTH)
    )
    last_name = (
        "" if "familyName" not in name else _required_text(name["familyName"], "familyName", NAME_FIELD_MAX_LENGTH)
    )

    return UserPatch(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        active=_active(document.get("active", True)),
        external_id=external_id,
    )


def _display_name(value: Any) -> str:
    if not isinstance(value, str):
        raise SCIMPatchError("displayName must be a string.")
    _reject_control_characters(value, "displayName")
    if not value.strip():
        raise SCIMPatchError("displayName must not be empty.")
    if len(value) > DISPLAY_NAME_MAX_LENGTH:
        raise SCIMPatchError(f"displayName exceeds maximum length of {DISPLAY_NAME_MAX_LENGTH} characters.")
    return value


def validate_display_name(value: Any) -> str:
    """Validate a provider Group displayName without database access."""
    return _display_name(value)


def _parse_group_remove(path: str, value: Any, *, value_present: bool) -> GroupMemberOperation:
    if not path:
        raise SCIMPatchError("remove operation requires a path", scim_type="noTarget")

    filtered_member_id = _member_filter_id(path)
    if filtered_member_id is not None:
        return GroupMemberOperation(op="remove", filter_member_id=filtered_member_id)
    if path.lower() == "members":
        if not value_present:
            return GroupMemberOperation(op="remove", clear_members=True)
        member_ids = _member_values(value)
        return GroupMemberOperation(op="remove", member_ids=member_ids)
    raise SCIMPatchError(f"Unsupported remove path: {path}")


def _nested_group_values(value: Any) -> tuple[Any, Any, Any]:
    if not isinstance(value, Mapping):
        return UNSET, UNSET, UNSET
    unknown_keys = set(value) - {"displayName", "externalId", "members"}
    if unknown_keys and ("displayName" in value or "externalId" in value or "members" in value):
        raise SCIMPatchError(f"Unsupported SCIM PATCH group keys: {sorted(unknown_keys)}")
    return value.get("displayName", UNSET), value.get("externalId", UNSET), value.get("members", UNSET)


def _external_id(value: Any) -> str:
    return _required_text(value, "externalId", EXTERNAL_ID_MAX_LENGTH)


def parse_external_id(value: Any) -> str:
    if value is None or value == "":
        return ""
    return _external_id(value)


def _parse_group_add_or_replace(
    operation_type: str,
    path: str,
    value: Any,
) -> tuple[Any, Any, GroupMemberOperation | None]:
    normalized_path = path.lower()
    if normalized_path == "displayname":
        return _display_name(value), UNSET, None
    if normalized_path == "externalid":
        return UNSET, _external_id(value), None
    if normalized_path == "members":
        if isinstance(value, Mapping) and "members" in value:
            unknown_keys = set(value) - {"members"}
            if unknown_keys:
                raise SCIMPatchError(f"Unsupported SCIM PATCH members keys: {sorted(unknown_keys)}")
            member_value = value["members"]
        else:
            member_value = value
        return UNSET, UNSET, GroupMemberOperation(op=operation_type, member_ids=_member_values(member_value))
    if path:
        raise SCIMPatchError(f"Unsupported SCIM PATCH path: {path}")

    nested_display_name, nested_external_id, nested_members = _nested_group_values(value)
    if nested_display_name is UNSET and nested_external_id is UNSET and nested_members is UNSET:
        return UNSET, UNSET, GroupMemberOperation(op=operation_type, member_ids=_member_values(value))

    display_name = UNSET if nested_display_name is UNSET else _display_name(nested_display_name)
    external_id = UNSET if nested_external_id is UNSET else _external_id(nested_external_id)
    member_operation = (
        None
        if nested_members is UNSET
        else GroupMemberOperation(op=operation_type, member_ids=_member_values(nested_members))
    )
    return display_name, external_id, member_operation


def _parse_group_operation(
    operation: Mapping[str, Any],
) -> tuple[Any, Any, GroupMemberOperation | None]:
    operation_type = _operation_type(operation)
    path = _operation_path(operation)
    value = _operation_value(operation, operation_type)
    if operation_type == "remove":
        if path.lower() == "externalid":
            return UNSET, "", None
        return UNSET, UNSET, _parse_group_remove(path, value, value_present="value" in operation)
    return _parse_group_add_or_replace(operation_type, path, value)


def parse_group_patch_operations(operations: Any) -> GroupPatch:
    """Parse provider ``/Groups/<id>`` PATCH operations without database access."""
    display_name: Any = UNSET
    external_id: Any = UNSET
    member_operations: list[GroupMemberOperation] = []
    unique_member_ids: set[int | str] = set()
    total_member_entries = 0

    for operation in _iter_operations(operations):
        operation_display_name, operation_external_id, member_operation = _parse_group_operation(operation)
        if operation_display_name is not UNSET:
            display_name = operation_display_name
        if operation_external_id is not UNSET:
            external_id = operation_external_id
        if member_operation is not None:
            total_member_entries += len(member_operation.member_ids) + int(
                member_operation.filter_member_id is not None
            )
            if total_member_entries > MAX_PATCH_MEMBERS:
                raise SCIMPatchError(f"Member entries must not exceed {MAX_PATCH_MEMBERS} values.")
            unique_member_ids.update(member_operation.member_ids)
            if member_operation.filter_member_id is not None:
                unique_member_ids.add(member_operation.filter_member_id)
            if len(unique_member_ids) > MAX_PATCH_MEMBERS:
                raise SCIMPatchError(f"Member IDs must not exceed {MAX_PATCH_MEMBERS} unique values.")
            member_operations.append(member_operation)

    return GroupPatch(
        display_name=display_name,
        external_id=external_id,
        member_operations=tuple(member_operations),
    )
