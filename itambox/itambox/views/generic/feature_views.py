from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ViewTab:
    label: str
    weight: int = 1000
    permission: Optional[str] = None
    badge: Optional[Callable] = None
    hide_if_empty: bool = False

    def render(self, instance, user):
        if self.permission and not user.has_perm(self.permission):
            return None
        badge_value = self._get_badge(instance)
        if self.badge and self.hide_if_empty and not badge_value:
            return None
        return {
            'label': self.label,
            'badge': badge_value,
            'weight': self.weight,
        }

    def _get_badge(self, instance):
        if not self.badge:
            return None
        if callable(self.badge):
            return self.badge(instance)
        return self.badge
