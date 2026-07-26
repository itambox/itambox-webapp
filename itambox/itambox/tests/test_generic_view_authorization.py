"""Tests for the shared view-authorization foundations (issue #82).

``PermissionResolver`` is the single implementation of ITAMbox's view
authorization rules, and ``SecuredObjectActionMixin`` is the single secured
object-action base shared by ``GenericTransactionView`` and ``SimplePostView``.

The contract these tests defend:

* a missing ``permission_required`` fails closed (``ImproperlyConfigured``) —
  it must never degrade into "any authenticated tenant member may act";
* ``permission_required = ()`` stays an explicit, supported opt-out for views
  that authorize per object themselves;
* the tenant boundary answers 404 rather than 403, so a pk's existence in
  another tenant is not disclosed;
* the shared code really is shared — the two service views inherit the *same*
  function objects rather than carrying near-identical copies.
"""

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase

from assets.models import Asset
from itambox.views.generic.authorization import PermissionResolver, SecuredObjectActionMixin
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView


class _User:
    def __init__(self, authenticated=True, allowed=True):
        self.is_authenticated = authenticated
        self.is_active = True
        self._allowed = allowed
        self.checked = []

    def has_perms(self, perms, obj=None):
        self.checked.append((tuple(perms), obj))
        return self._allowed


class _StubView:
    """Minimal stand-in exposing only what PermissionResolver reads."""

    def __init__(self, obj=None, raises=None, user=None, permission_required=None):
        self._obj = obj
        self._raises = raises
        self.permission_required = permission_required
        self.request = RequestFactory().post("/x/")
        self.request.user = user or _User()

    def get_object(self):
        if self._raises is not None:
            raise self._raises
        return self._obj


# ---------------------------------------------------------------------------
# PermissionResolver
# ---------------------------------------------------------------------------


class PermissionCodenameTests(SimpleTestCase):
    def test_codename_is_app_label_action_model(self):
        self.assertEqual(PermissionResolver.permission_codename(Asset, "view"), "assets.view_asset")
        self.assertEqual(PermissionResolver.permission_codename(Asset, "change"), "assets.change_asset")

    def test_model_permissions_returns_a_single_element_tuple(self):
        self.assertEqual(PermissionResolver.model_permissions(Asset, "delete"), ("assets.delete_asset",))

    def test_unknown_model_yields_the_ungrantable_empty_permission(self):
        """``("",)`` is the historical "deny" sentinel: it is a *non-empty* tuple
        so the check still runs, but no backend can ever grant ``""``."""
        result = PermissionResolver.model_permissions(None, "view")
        self.assertEqual(result, ("",))
        self.assertTrue(result, "the sentinel must stay truthy or it would read as 'no perms required'")


class DeclaredPermissionsTests(SimpleTestCase):
    def test_none_fails_closed(self):
        view = _StubView(permission_required=None)
        with self.assertRaises(ImproperlyConfigured) as ctx:
            PermissionResolver.declared_permissions(view)
        self.assertIn("permission_required", str(ctx.exception))

    def test_empty_tuple_is_a_supported_self_authorization_optout(self):
        view = _StubView(permission_required=())
        self.assertEqual(PermissionResolver.declared_permissions(view), ())

    def test_string_is_normalized_to_a_tuple(self):
        view = _StubView(permission_required="app.change_thing")
        self.assertEqual(PermissionResolver.declared_permissions(view), ("app.change_thing",))

    def test_tuple_and_list_pass_through(self):
        self.assertEqual(
            PermissionResolver.declared_permissions(_StubView(permission_required=("a.b", "c.d"))),
            ("a.b", "c.d"),
        )
        self.assertEqual(
            PermissionResolver.declared_permissions(_StubView(permission_required=["a.b"])),
            ["a.b"],
        )


class ObjectUnderCheckTests(SimpleTestCase):
    def test_returns_the_view_object(self):
        sentinel = object()
        self.assertIs(PermissionResolver.object_under_check(_StubView(obj=sentinel)), sentinel)

    def test_authenticated_user_gets_the_404_reraised(self):
        """Outside the tenant scope the request must 404, not fall through to a
        403 that would confirm the pk exists somewhere else."""
        view = _StubView(raises=Http404(), user=_User(authenticated=True))
        with self.assertRaises(Http404):
            PermissionResolver.object_under_check(view)

    def test_anonymous_user_falls_through_to_the_login_redirect(self):
        view = _StubView(raises=Http404(), user=_User(authenticated=False))
        self.assertIsNone(PermissionResolver.object_under_check(view))


class HasObjectPermissionTests(SimpleTestCase):
    def test_empty_permissions_short_circuit_to_allowed(self):
        """A self-authorizing view (``permission_required = ()``) passes the
        declarative gate without any object lookup at all."""

        class _Exploding(_StubView):
            def get_object(self):
                raise AssertionError("get_object() must not run for the () opt-out")

        self.assertTrue(PermissionResolver.has_object_permission(_Exploding(), ()))

    def test_permissions_are_checked_against_the_object(self):
        obj = object()
        user = _User(allowed=True)
        view = _StubView(obj=obj, user=user)

        self.assertTrue(PermissionResolver.has_object_permission(view, ("assets.change_asset",)))
        self.assertEqual(user.checked, [(("assets.change_asset",), obj)])

    def test_denial_is_propagated(self):
        view = _StubView(obj=object(), user=_User(allowed=False))
        self.assertFalse(PermissionResolver.has_object_permission(view, ("assets.change_asset",)))


# ---------------------------------------------------------------------------
# SecuredObjectActionMixin — one implementation, two service views
# ---------------------------------------------------------------------------


class SharedSecuredActionMixinTests(SimpleTestCase):
    """Acceptance: "shared authorization code has one implementation"."""

    SERVICE_VIEWS = (GenericTransactionView, SimplePostView)

    def test_both_service_views_are_built_on_the_shared_mixin(self):
        for cls in self.SERVICE_VIEWS:
            with self.subTest(view=cls.__name__):
                self.assertIn(SecuredObjectActionMixin, cls.__mro__)

    def test_authorization_methods_are_literally_the_same_function(self):
        """Identity, not just equivalence: a copy-paste divergence would fail."""
        for name in ("get_permission_required", "has_permission", "get_queryset", "get_object"):
            shared = getattr(SecuredObjectActionMixin, name)
            for cls in self.SERVICE_VIEWS:
                with self.subTest(method=name, view=cls.__name__):
                    self.assertIs(
                        getattr(cls, name),
                        shared,
                        f"{cls.__name__}.{name} is not the shared implementation",
                    )

    def test_fail_closed_contract_survives_on_the_mixin_itself(self):
        view = SecuredObjectActionMixin()
        view.permission_required = None
        with self.assertRaises(ImproperlyConfigured):
            view.get_permission_required()

    def test_missing_queryset_is_a_configuration_error(self):
        view = SecuredObjectActionMixin()
        view.queryset = None
        with self.assertRaises(ImproperlyConfigured):
            view.get_queryset()

    def test_simple_post_view_keeps_requiring_a_pk_or_an_override(self):
        """``SimplePostView`` has no form/queryset fallback, so a route without a
        ``pk`` is a programming error rather than a 404."""
        view = SimplePostView()
        view.kwargs = {}
        view.queryset = Asset.objects.all()
        with self.assertRaises(NotImplementedError):
            view.get_object()

    def test_transaction_view_keeps_its_lookup_behaviour_without_a_pk(self):
        """``GenericTransactionView`` historically performed the lookup anyway
        (which 404s); that behaviour is preserved, not silently upgraded."""
        view = GenericTransactionView()
        view.kwargs = {}
        view.queryset = Asset.objects.all()
        self.assertFalse(view.strict_pk_required)
