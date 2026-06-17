import logging

from itambox.middleware import get_current_request_id


class RequestIDFilter(logging.Filter):
    """Inject the per-request id into every log record.

    ``get_current_request_id`` reads the ``_request_id`` contextvar that
    ``CurrentUserMiddleware`` sets at the start of each HTTP request. Outside a
    request (background tasks, management commands, startup) it returns ``None``,
    so we fall back to ``'-'`` to keep the formatter token populated.
    """

    def filter(self, record):
        record.request_id = get_current_request_id() or '-'
        return True
