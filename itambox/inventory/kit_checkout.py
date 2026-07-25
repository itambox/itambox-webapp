"""Dispatch seam for the kit checkout operation -- a runtime leaf (issue #87, phase D).

``checkout_kit`` allocates assets, asset assignments and status labels, so it
lives in ``assets.services``. ``Kit.checkout_to_holder`` is the model-level
convenience wrapper around it, and importing ``assets.services`` from
``inventory.models`` closes a cycle: ``assets.services`` imports
``inventory.models`` and ``inventory.services`` at module scope. That back edge
used to be hidden behind a function-body import inside the method.

Instead the model layer depends on this module, which imports nothing
first-party. ``AssetsConfig.ready()`` registers the concrete implementation once
the app registry is populated, so the wiring is deterministic rather than
dependent on whichever module happened to be imported first.
"""

from typing import Any, Callable, Optional

from django.core.exceptions import ImproperlyConfigured

_implementation: Optional[Callable[..., Any]] = None


def register_kit_checkout(func: Callable[..., Any]) -> Callable[..., Any]:
    """Register the concrete kit-checkout implementation.

    Called exactly once, from ``AssetsConfig.ready()``. Returns ``func`` so it
    can also be used as a decorator.
    """
    global _implementation
    _implementation = func
    return func


def get_kit_checkout() -> Callable[..., Any]:
    """Return the registered implementation, or fail loudly if there is none."""
    if _implementation is None:
        raise ImproperlyConfigured(
            "No kit checkout implementation is registered. The 'assets' app provides it from "
            "AssetsConfig.ready(); ensure 'assets' is in INSTALLED_APPS and the app registry is ready."
        )
    return _implementation


def checkout_kit(kit: Any, **kwargs: Any) -> Any:
    """Invoke the registered kit-checkout implementation."""
    return get_kit_checkout()(kit, **kwargs)
