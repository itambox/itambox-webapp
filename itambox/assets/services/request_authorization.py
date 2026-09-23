"""Shared authorization checks for asset request actions."""

from typing import Any

from ..models import AssetRequest

REQUEST_ACTION_PERMISSIONS: dict[str, str] = {
    "approve": "assets.approve_assetrequest",
    "deny": "assets.approve_assetrequest",
    "cancel": "assets.approve_assetrequest",
    "claim": "assets.fulfill_assetrequest",
    "fulfill": "assets.fulfill_assetrequest",
    "mark_fulfilled": "assets.fulfill_assetrequest",
    "bulk_receive": "assets.fulfill_assetrequest",
}

STAFF_BYPASS_ACTIONS: frozenset[str] = frozenset({"cancel", "claim", "mark_fulfilled"})


def _is_self_service_action(user: Any, asset_request: AssetRequest, action: str) -> bool:
    if action == "cancel":
        return asset_request.requester_id == user.pk
    if action == "claim":
        assigned_user = asset_request.assigned_user
        return asset_request.requester_id == user.pk or (assigned_user is not None and assigned_user.user_id == user.pk)
    return False


def is_self_service_claim(user: Any, asset_request: AssetRequest) -> bool:
    """Whether this actor claims their own requested or assigned request."""
    return _is_self_service_action(user, asset_request, "claim")


def _has_staff_bypass(user: Any, action: str) -> bool:
    # Django's is_staff is the global flag, not the RBAC "Staff" badge (managed reach).
    return action in STAFF_BYPASS_ACTIONS and bool(getattr(user, "is_staff", False))


def can_asset_request_action(user: Any, asset_request: AssetRequest, action: str) -> bool:
    """Return whether this actor may perform an action on the target request."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False

    if _is_self_service_action(user, asset_request, action):
        return True

    permission = REQUEST_ACTION_PERMISSIONS.get(action)
    if permission is None:
        return False

    if _has_staff_bypass(user, action):
        return True

    if asset_request.tenant_id is None:
        return False

    return user.has_perm(permission, obj=asset_request)
