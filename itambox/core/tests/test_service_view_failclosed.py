"""B3: the service-view base classes must FAIL CLOSED — a missing
permission_required is a developer error (raises ImproperlyConfigured), while an
explicit empty tuple () opts a view into doing its own per-object authorization.
"""
import json

from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import RequestFactory, SimpleTestCase

from itambox.views.generic.service_views import GenericTransactionView, SimplePostView


class FailClosedServiceViewTests(SimpleTestCase):
    def test_generic_transaction_view_raises_when_unset(self):
        view = GenericTransactionView()
        view.permission_required = None
        with self.assertRaises(ImproperlyConfigured):
            view.get_permission_required()

    def test_simple_post_view_raises_when_unset(self):
        view = SimplePostView()
        view.permission_required = None
        with self.assertRaises(ImproperlyConfigured):
            view.get_permission_required()

    def test_empty_tuple_is_self_authz_optout(self):
        for cls in (GenericTransactionView, SimplePostView):
            view = cls()
            view.permission_required = ()
            self.assertEqual(view.get_permission_required(), ())

    def test_string_is_normalized_to_tuple(self):
        view = GenericTransactionView()
        view.permission_required = 'app.change_thing'
        self.assertEqual(view.get_permission_required(), ('app.change_thing',))


class _AuthedUser:
    """Minimal authenticated user stand-in (avoids a DB hit for these unit tests)."""
    is_authenticated = True
    is_active = True

    def has_perms(self, perms, obj=None):
        return True


class _DenyingView(SimplePostView):
    """A self-authorizing SimplePostView whose action denies per-object."""
    permission_required = ()

    def get_object(self):
        return object()

    def perform_action(self, obj, request):
        raise PermissionDenied("nope, not yours")


class SimplePostViewPermissionDeniedTests(SimpleTestCase):
    """Audit fix D: a PermissionDenied raised inside perform_action surfaces as a
    toast for HTMX requests, but re-raises (→ standard 403) for full-page ones."""

    def setUp(self):
        self.factory = RequestFactory()

    def _make_request(self, htmx):
        request = self.factory.post('/x/')
        request.user = _AuthedUser()
        request.htmx = htmx
        return request

    def test_htmx_request_gets_error_toast_not_403_page(self):
        response = _DenyingView.as_view()(self._make_request(htmx=True))
        self.assertEqual(response.status_code, 204)
        trigger = json.loads(response['HX-Trigger'])
        self.assertEqual(trigger['showMessage']['level'], 'danger')
        self.assertIn('nope', trigger['showMessage']['message'])

    def test_full_page_request_reraises_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            _DenyingView.as_view()(self._make_request(htmx=False))
