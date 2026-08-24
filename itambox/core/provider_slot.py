"""A domain-blind, single-provider lifecycle primitive.

The slot owns one immutable process default and a ContextVar-local override.
Tenant-specific contracts compose this primitive rather than reimplementing its
lifecycle or introducing a keyed registry.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Generic, TypeVar, cast

from django.core.exceptions import ImproperlyConfigured

T = TypeVar("T")
_UNSET = object()


class SingleProviderSlot(Generic[T]):
    """Store one provider with identity-safe registration and local overrides."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._default: T | object = _UNSET
        self._lock = RLock()
        self._override: contextvars.ContextVar[T | object] = contextvars.ContextVar(
            f"{name.replace(' ', '_')}_provider_override",
            default=_UNSET,
        )

    def register(self, provider: T) -> None:
        """Install the process default, rejecting every competing object."""
        with self._lock:
            if self._default is _UNSET:
                self._default = provider
                return
            if self._default is provider:
                return
            raise ImproperlyConfigured(f"{self._name} provider is already configured with a different object")

    def get(self) -> T:
        """Return the current ContextVar override or configured process default."""
        override = self._override.get()
        if override is not _UNSET:
            return cast(T, override)

        with self._lock:
            default = self._default
        if default is _UNSET:
            raise ImproperlyConfigured(f"{self._name} provider is not configured")
        return cast(T, default)

    @contextmanager
    def override(self, provider: T) -> Iterator[None]:
        """Temporarily bind a provider in the current execution context."""
        token = self._override.set(provider)
        try:
            yield
        finally:
            self._override.reset(token)
