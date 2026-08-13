"""Compatibility exports for the kernel-owned authorization cache contract.

The implementation lives in ``core.authorization_cache``. This module remains
for callers that still import the historical ``core.auth.cache`` path, while
preserving its patchable cache/logger objects for legacy tests and integrations.
"""

from core.authorization_cache import *  # noqa: F401,F403
from core.authorization_cache import cache, logger
