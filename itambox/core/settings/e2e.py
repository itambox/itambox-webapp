"""Settings for the disposable GitHub Actions Playwright environment.

This module inherits the normal development contract but raises the login
request budget enough for the four isolated role setup projects.  Production
and ordinary development settings keep the default rate-limit policy.
"""

from .dev import *

RATELIMIT_LIMIT = 100
RATELIMIT_PERIOD = 60
