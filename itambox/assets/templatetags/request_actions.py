"""Template tags for asset request action visibility."""

from typing import Any

from django import template

from assets.models import AssetRequest
from assets.services.request_authorization import can_asset_request_action as _can_asset_request_action

register = template.Library()


@register.simple_tag(name="can_asset_request_action")
def request_action_allowed(user: Any, asset_request: AssetRequest, action: str) -> bool:
    """Expose the shared asset request action predicate to templates."""
    return _can_asset_request_action(user, asset_request, action)
