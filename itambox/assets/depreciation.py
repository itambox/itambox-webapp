"""Active domain-service facade for asset depreciation calculations.

The pure implementation lives in ``assets.model_book_value`` so model code can
use it without depending on this service-layer module.  Tasks, services and
presentation callers intentionally continue to use this facade.
"""

from .model_book_value import compute_book_value, resolve_policy

__all__ = ["compute_book_value", "resolve_policy"]
