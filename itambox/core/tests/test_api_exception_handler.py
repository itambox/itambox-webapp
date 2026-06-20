"""Audit fix B: the custom DRF exception handler maps Django's model-level
``ValidationError`` (raised by ``clean()`` / the validate-on-save signal) to an
HTTP 400 instead of letting it surface as an unhandled 500.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import SimpleTestCase
from rest_framework import status

from itambox.api.exceptions import itambox_exception_handler


class ITAMBoxExceptionHandlerTests(SimpleTestCase):
    def test_django_validation_error_message_dict_maps_to_400(self):
        exc = DjangoValidationError({'location': ['Belongs to another tenant.']})
        response = itambox_exception_handler(exc, {})
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('location', response.data)

    def test_django_validation_error_message_list_maps_to_400(self):
        exc = DjangoValidationError(['Cross-tenant reference rejected.'])
        response = itambox_exception_handler(exc, {})
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_django_validation_error_plain_string_maps_to_400(self):
        exc = DjangoValidationError('nope')
        response = itambox_exception_handler(exc, {})
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_validation_exception_delegates_to_drf(self):
        # An unrelated exception the stock handler doesn't know about returns None
        # (DRF then re-raises → 500), proving we don't swallow everything as 400.
        response = itambox_exception_handler(RuntimeError('boom'), {})
        self.assertIsNone(response)
