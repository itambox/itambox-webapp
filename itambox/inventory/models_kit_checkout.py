"""Model-owned port for kit checkout behavior."""

from collections.abc import Mapping
from typing import Protocol

from core.context import SystemAuthorizationContext
from core.provider_slot import SingleProviderSlot


class KitCheckoutProvider(Protocol):
    """The concrete callable supplied by the assets application."""

    def __call__(
        self,
        kit: object,
        holder: object | None = None,
        location: object | None = None,
        user: object | None = None,
        notes: str = "",
        source_location: object | None = None,
        request: object | None = None,
        system_authorizations: Mapping[str, SystemAuthorizationContext] | None = None,
        **kwargs: object,
    ) -> object:
        pass


_provider = SingleProviderSlot[KitCheckoutProvider]("inventory kit checkout")


def register_kit_checkout(provider: KitCheckoutProvider) -> KitCheckoutProvider:
    """Register the assets-owned implementation once the apps are ready."""
    _provider.register(provider)
    return provider


def get_kit_checkout() -> KitCheckoutProvider:
    """Return the registered implementation or fail loudly."""
    return _provider.get()


def checkout_kit(
    kit: object,
    holder: object | None = None,
    location: object | None = None,
    user: object | None = None,
    notes: str = "",
    source_location: object | None = None,
    request: object | None = None,
    system_authorizations: Mapping[str, SystemAuthorizationContext] | None = None,
    **kwargs: object,
) -> object:
    """Invoke the registered assets-owned kit checkout implementation."""
    return get_kit_checkout()(
        kit,
        holder=holder,
        location=location,
        user=user,
        notes=notes,
        source_location=source_location,
        request=request,
        system_authorizations=system_authorizations,
        **kwargs,
    )
