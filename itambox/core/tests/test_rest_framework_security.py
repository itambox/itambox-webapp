"""Regression coverage for the request and rendering boundaries fixed in DRF 3.17.2."""

import json

from django.core.exceptions import RequestDataTooBig
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncRequestFactory, SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.renderers import AdminRenderer
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView


@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=64)
class RequestDataSizeLimitTests(SimpleTestCase):
    def test_json_request_data_enforces_the_limit_for_wsgi_and_asgi(self):
        # Keep each input tiny; the exact boundary, not memory exhaustion, is the oracle.
        prefix = b'{"value":"'
        suffix = b'"}'
        value = b"x" * (64 - len(prefix) - len(suffix))
        for factory in (APIRequestFactory(), AsyncRequestFactory()):
            with self.subTest(request_factory=type(factory).__name__):
                raw = factory.generic("POST", "/", prefix + value + suffix, content_type="application/json")
                request = APIView().initialize_request(raw)
                self.assertEqual(request.data, {"value": value.decode()})
                raw = factory.generic("POST", "/", prefix + value + b"x" + suffix, content_type="application/json")
                request = APIView().initialize_request(raw)
                with self.assertRaises(RequestDataTooBig):
                    _ = request.data

    def test_urlencoded_request_data_enforces_the_limit(self):
        factory = APIRequestFactory()
        value = "x" * (64 - len("value="))
        raw = factory.generic("POST", "/", f"value={value}", content_type="application/x-www-form-urlencoded")
        self.assertEqual(APIView().initialize_request(raw).data["value"], value)
        raw = factory.generic("POST", "/", f"value={value}x", content_type="application/x-www-form-urlencoded")
        with self.assertRaises(RequestDataTooBig):
            _ = APIView().initialize_request(raw).data

    def test_json_limit_counts_encoded_bytes_not_unicode_characters(self):
        text = json.dumps({"value": "ä" * 30}, ensure_ascii=False)
        self.assertLess(len(text), 64)
        self.assertGreater(len(text.encode("utf-8")), 64)
        raw = APIRequestFactory().generic("POST", "/", text.encode("utf-8"), content_type="application/json")
        with self.assertRaises(RequestDataTooBig):
            _ = APIView().initialize_request(raw).data

    def test_multipart_files_keep_the_separate_file_upload_path(self):
        # DATA_UPLOAD_MAX_MEMORY_SIZE excludes file bytes. Do not turn the JSON fix
        # into a blanket body cap that breaks the application's supported uploads.
        content = b"x" * 256
        upload = SimpleUploadedFile("attachment.txt", content, content_type="text/plain")
        raw = APIRequestFactory().post("/", {"upload": upload}, format="multipart")
        parsed = APIView().initialize_request(raw).data["upload"]
        try:
            self.assertEqual(parsed.size, len(content))
            self.assertEqual(parsed.read(), content)
        finally:
            parsed.close()


class _WritePermission(BasePermission):
    def has_permission(self, request, view):
        return request.method == "POST" or (request.method == "GET" and view.allow_get)


class _AdminRenderProbe(APIView):
    # AdminRenderer is not enabled by ITAMbox. Exercise the conditional upstream
    # vulnerability without enabling that renderer on any production endpoint.
    authentication_classes = ()
    permission_classes = (_WritePermission,)
    renderer_classes = (AdminRenderer,)
    throttle_classes = ()
    allow_get = False
    get_calls = 0

    def get(self, request):
        self.get_calls += 1
        return Response({"name": "get-only-record-canary"})

    def post(self, request):
        raise ValidationError({"name": ["Required field is missing."]})


class AdminRendererPermissionTests(SimpleTestCase):
    def test_invalid_write_cannot_read_a_get_protected_representation(self):
        factory = APIRequestFactory()
        view = _AdminRenderProbe.as_view()
        denied = view(factory.get("/", HTTP_ACCEPT="text/html"))
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.renderer_context["view"].get_calls, 0)

        response = view(factory.post("/", {}, format="json", HTTP_ACCEPT="text/html"))
        response.render()
        self.assertEqual(response.renderer_context["view"].get_calls, 0)
        self.assertNotContains(response, "get-only-record-canary", status_code=400)
        self.assertContains(response, "Required field is missing.", status_code=400)

    def test_invalid_write_still_renders_an_authorized_get_representation(self):
        view = _AdminRenderProbe.as_view(allow_get=True)
        response = view(APIRequestFactory().post("/", {}, format="json", HTTP_ACCEPT="text/html"))
        response.render()
        self.assertEqual(response.renderer_context["view"].get_calls, 1)
        self.assertContains(response, "get-only-record-canary", status_code=400)
