"""Model-owned port for software reconciliation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

from core.provider_slot import SingleProviderSlot

if TYPE_CHECKING:
    from .models import Software


class SoftwareReconciliationResult(TypedDict):
    """Stable result shape returned by the licenses-owned implementation."""

    software_id: int
    software_name: str
    installed_count: int
    entitled_seats: int
    delta: int
    compliant: bool
    status: str
    linked_seats: int


class SoftwareReconciliationProvider(Protocol):
    def __call__(self, software: Software) -> SoftwareReconciliationResult: ...


_provider = SingleProviderSlot[SoftwareReconciliationProvider]("software reconciliation")


def register_software_reconciliation(provider: SoftwareReconciliationProvider) -> None:
    """Register the concrete reconciliation implementation."""
    _provider.register(provider)


def get_software_reconciliation_provider() -> SoftwareReconciliationProvider:
    """Return the concrete provider or fail loudly when startup wiring is absent."""
    return _provider.get()


def override_software_reconciliation(provider: SoftwareReconciliationProvider):
    """Temporarily override the provider in the current execution context."""
    return _provider.override(provider)


def reconcile_software(software: Software) -> SoftwareReconciliationResult:
    """Delegate model convenience calls to the licenses-owned implementation."""
    return get_software_reconciliation_provider()(software)
