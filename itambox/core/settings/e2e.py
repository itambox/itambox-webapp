"""Settings for the disposable GitHub Actions Playwright environment.

This module inherits the normal development contract but raises the login
request budget enough for the four isolated role setup projects.  Production
and ordinary development settings keep the default rate-limit policy.
"""

from . import dev as _dev

globals().update({name: getattr(_dev, name) for name in dir(_dev) if name.isupper()})

RATELIMIT_LIMIT = 100
RATELIMIT_PERIOD = 60
