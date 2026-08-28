"""Model-owned port for subscription seat usage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from core.provider_slot import SingleProviderSlot

if TYPE_CHECKING:
    from .models import Subscription


class SubscriptionSeatUsageProvider(Protocol):
    def __call__(self, subscription: Subscription) -> int:
        pass


_provider = SingleProviderSlot[SubscriptionSeatUsageProvider]("subscription seat usage")


def register_seat_usage(provider: SubscriptionSeatUsageProvider) -> None:
    """Register the subscriptions-owned seat usage adapter."""
    _provider.register(provider)


def get_seat_usage_provider() -> SubscriptionSeatUsageProvider:
    """Return the registered seat usage adapter."""
    return _provider.get()


def override_seat_usage(provider: SubscriptionSeatUsageProvider):
    """Temporarily override seat usage in the current execution context."""
    return _provider.override(provider)


def get_assigned_seats(subscription: Subscription) -> int:
    """Delegate the model convenience property to the service-owned adapter."""
    return get_seat_usage_provider()(subscription)
