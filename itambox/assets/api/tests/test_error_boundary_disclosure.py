"""Boundary regression: internal exception text must never become a REST message."""

from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.test import APITestCase

from assets.api.serializer_mixins import CanonicalSpecificationSerializerMixin
from assets.api.specification_api import explicit_fieldset_selection
from assets.api.type_library import _command_error_response
from assets.services.type_library.commands import LibraryCommandError

INTERNAL_SENTINEL = "INTERNAL-SECRET-SENTINEL-DO-NOT-EXPOSE"


class CanonicalSerializerErrorBoundaryTests(APITestCase):
    def test_command_error_hides_permission_detail(self):
        with self.assertRaises(DRFPermissionDenied) as raised:
            CanonicalSpecificationSerializerMixin._command_error(PermissionDenied(INTERNAL_SENTINEL))
        self.assertNotIn(INTERNAL_SENTINEL, str(raised.exception.detail))
        self.assertIn("permission", str(raised.exception.detail).lower())

    def test_command_error_translates_django_validation_to_stable_field_messages(self):
        with self.assertRaises(serializers.ValidationError) as raised:
            CanonicalSpecificationSerializerMixin._command_error(
                DjangoValidationError({"internal_field": INTERNAL_SENTINEL})
            )
        detail = raised.exception.detail
        self.assertNotIn(INTERNAL_SENTINEL, str(detail))
        self.assertEqual(detail, {"internal_field": "Invalid value."})

    def test_command_error_translates_non_field_validation_to_a_stable_message(self):
        with self.assertRaises(serializers.ValidationError) as raised:
            CanonicalSpecificationSerializerMixin._command_error(DjangoValidationError(INTERNAL_SENTINEL))
        self.assertNotIn(INTERNAL_SENTINEL, str(raised.exception.detail))


class FieldsetSelectionBoundaryTests(APITestCase):
    def test_fieldset_selection_does_not_leak_dto_detail(self):
        with self.assertRaises(serializers.ValidationError) as raised:
            explicit_fieldset_selection(["not-qualified"])
        self.assertNotIn("not-qualified", str(raised.exception.detail))
        self.assertEqual(raised.exception.detail, {"fieldsets": "Invalid fieldset selection."})

    def test_create_fieldset_selection_does_not_leak_dto_detail(self):
        from assets.api.specification_api import create_fieldset_selection_from_values

        with self.assertRaises(serializers.ValidationError) as raised:
            create_fieldset_selection_from_values(["not-qualified"], omitted=False)
        self.assertEqual(raised.exception.detail, {"fieldsets": "Invalid fieldset selection."})


class TypeLibraryErrorBoundaryTests(APITestCase):
    def test_command_issue_messages_are_public_only(self):
        error = LibraryCommandError(
            "REFERENCE_CONFLICT",
            path=("catalog", "laptops"),
            issues=(
                SimpleNamespace(
                    code="REFERENCE_CONFLICT",
                    path=("catalog", "laptops"),
                    message=INTERNAL_SENTINEL,
                ),
            ),
        )
        response = _command_error_response(error)
        body = response.content.decode()
        self.assertNotIn(INTERNAL_SENTINEL, body)
        self.assertEqual(response.status_code, 409)
        data = response.data["error"]
        self.assertEqual(data["code"], "REFERENCE_CONFLICT")
        self.assertEqual(data["message"], "The submitted library conflicts with existing state.")
        self.assertEqual(data["issues"][0]["path"], ["catalog", "laptops"])
        self.assertEqual(data["issues"][0]["message"], "The submitted library conflicts with existing state.")

    def test_fallback_exception_text_is_public_only(self):
        error = LibraryCommandError(
            "UNSUPPORTED_STRUCTURE",
            path=("catalog",),
            message=INTERNAL_SENTINEL,
        )
        response = _command_error_response(error)
        self.assertNotIn(INTERNAL_SENTINEL, response.content.decode())
        self.assertEqual(response.status_code, 409)

    def test_unknown_code_falls_back_to_the_generic_public_message(self):
        error = SimpleNamespace(code="MYSTERY_CODE", path=(), issues=(), message=INTERNAL_SENTINEL)
        response = _command_error_response(error)
        body = response.content.decode()
        self.assertNotIn(INTERNAL_SENTINEL, body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["message"], "The submitted library request is invalid.")
