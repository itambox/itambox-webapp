from rest_framework import status
from rest_framework.exceptions import APIException


class SerializerNotFound(Exception):
    pass


class PreconditionRequired(APIException):
    """Raised when a mutating request omits the required If-Match header.

    Maps to HTTP 428 Precondition Required (RFC 6585). The ``etag`` of the
    current resource state is stored so the caller can echo it back to the
    client as a starting concurrency token.
    """

    status_code = status.HTTP_428_PRECONDITION_REQUIRED
    default_detail = 'If-Match header is required for mutating requests.'
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
    default_detail = 'The resource has changed since it was last fetched (stale ETag).'
    default_code = 'precondition_failed'

    def __init__(self, detail=None, etag=None):
        self.etag = etag
        super().__init__(detail)
