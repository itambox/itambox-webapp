from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError as DRFValidationError
from rest_framework.views import exception_handler as drf_exception_handler


class SerializerNotFound(Exception):
    pass


def itambox_exception_handler(exc, context):
    """DRF exception handler that maps Django's ``ValidationError`` to HTTP 400.

    Model-level validation runs on every save via the ``validate_custom_validators_on_save``
    pre_save signal (it calls ``instance.clean()``) and via model ``clean()`` overrides
    that enforce tenant-boundary FK checks. Those raise a *Django* ``ValidationError``,
    which DRF's stock handler does not recognise — it would surface as an unhandled 500.
    Translate it to a DRF ``ValidationError`` so the client gets a 400 with the field
    errors instead of a server error.
    """
    if isinstance(exc, DjangoValidationError):
        if hasattr(exc, 'message_dict'):
            detail = exc.message_dict
        elif hasattr(exc, 'messages'):
            detail = exc.messages
        else:
            detail = str(exc)
        exc = DRFValidationError(detail=detail)
    return drf_exception_handler(exc, context)


class PreconditionRequired(APIException):
    """Raised when a mutating request omits the required If-Match header.

    Maps to HTTP 428 Precondition Required (RFC 6585). The ``etag`` of the
    current resource state is stored so the caller can echo it back to the
    client as a starting concurrency token.
    """

    status_code = status.HTTP_428_PRECONDITION_REQUIRED
    default_detail = _('If-Match header is required for mutating requests.')
    default_code = 'precondition_required'

    def __init__(self, detail=None, etag=None):
        self.etag = etag
        super().__init__(detail)


class PreconditionFailed(APIException):
    """Raised when the supplied If-Match token no longer matches the resource.

    Maps to HTTP 412 Precondition Failed — a stale ETag / concurrency conflict.
    The current ``etag`` is stored so the caller can return it for the client to
    retry with.
    """

    status_code = status.HTTP_412_PRECONDITION_FAILED
    default_detail = _('The resource has changed since it was last fetched (stale ETag).')
    default_code = 'precondition_failed'

    def __init__(self, detail=None, etag=None):
        self.etag = etag
        super().__init__(detail)
