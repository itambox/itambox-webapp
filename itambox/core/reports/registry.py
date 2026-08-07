"""Runtime discovery and lookup for domain report providers."""

from threading import RLock

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import autodiscover_modules

from core.reports.contracts import PUBLIC_REPORT_TYPES, ReportDefinition

_registry: dict[str, ReportDefinition] = {}
_discovery_lock = RLock()
_discovered = False


def register_report_provider(provider: ReportDefinition) -> None:
    """Register one provider for each stable report identifier it supports."""
    report_types = getattr(provider, "report_types", None)
    if report_types is None:
        report_types = (provider.report_type,)

    with _discovery_lock:
        for report_type in report_types:
            existing = _registry.get(report_type)
            if existing is not None and existing is not provider:
                raise ImproperlyConfigured(f"Duplicate report provider for {report_type!r}.")
        for report_type in report_types:
            _registry[report_type] = provider


def discover_report_providers() -> None:
    """Import every installed application's ``reports`` module.

    Discovery is lazy so importing ``core.reports`` remains safe during Django
    app loading, but it is idempotent and fails loudly on an import,
    registration, or coverage error at the first real compilation rather than
    silently rendering the wrong report.
    """
    global _discovered
    with _discovery_lock:
        if _discovered:
            return

        autodiscover_modules("reports")

        missing = set(PUBLIC_REPORT_TYPES) - set(_registry)
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise ImproperlyConfigured(f"No report provider registered for: {missing_list}")
        _discovered = True


def get_report_provider(report_type: str) -> ReportDefinition:
    discover_report_providers()
    try:
        return _registry[report_type]
    except KeyError as error:
        raise ValueError(f"Unsupported report type: {report_type}") from error


def get_registered_report_types() -> tuple[str, ...]:
    discover_report_providers()
    return tuple(_registry)
