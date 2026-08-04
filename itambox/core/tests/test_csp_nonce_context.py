from types import SimpleNamespace

from django.http import HttpResponse
from django.test import SimpleTestCase

from core.context import get_current_csp_nonce
from itambox.middleware import CSPMiddleware


class CSPNonceContextTests(SimpleTestCase):
    def test_nonce_is_available_during_response_and_reset_afterwards(self):
        observed = []

        def get_response(request):
            observed.append(get_current_csp_nonce())
            return HttpResponse("ok")

        response = CSPMiddleware(get_response)(SimpleNamespace())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(observed), 1)
        self.assertRegex(observed[0], r"^[A-Za-z0-9+/=_-]+$")
        self.assertIsNone(get_current_csp_nonce())

    def test_htmx_request_reuses_parent_document_nonce(self):
        observed = []
        parent_nonce = "parentNonce_123"

        def get_response(request):
            observed.append(request.csp_nonce)
            return HttpResponse("fragment")

        request = SimpleNamespace(headers={"HX-Request": "true", "X-CSP-Nonce": parent_nonce})
        response = CSPMiddleware(get_response)(request)

        self.assertEqual(observed, [parent_nonce])
        self.assertIn(f"'nonce-{parent_nonce}'", response["Content-Security-Policy"])
