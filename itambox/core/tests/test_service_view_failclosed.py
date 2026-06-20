"""B3: the service-view base classes must FAIL CLOSED — a missing
permission_required is a developer error (raises ImproperlyConfigured), while an
explicit empty tuple () opts a view into doing its own per-object authorization.
"""
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

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
