"""Issue #499: the workspace switcher keeps list context across scope switches.

The switcher links are built from the request by the context processor; these
tests pin the carried query string, the object-page landing target, and the
visible filter-reset notice.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from organization.models import Tenant
from organization.views.context_processors import tenant_switcher_processor

User = get_user_model()


class ScopeSwitchLinkTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="scope-switch-user", password="pw")
        self.tenant = Tenant.objects.create(name="Scope Switch Tenant", slug="scope-switch-tenant")

    def _context(self, path, user=None):
        request = self.factory.get(path)
        request.user = user or self.user
        request.resolver_match = resolve(path.split("?")[0])
        return tenant_switcher_processor(request)["scope_switch"]

    def test_list_switch_carries_filters_and_resets_pagination(self):
        context = self._context(
            f"/assets/assets/?q=laptop&status=deployed&page=2&switch_all_accessible=1&tenant={self.tenant.pk}"
        )

        self.assertEqual(context["base_url"], reverse("assets:asset_list"))
        self.assertEqual(context["suffix"], f"&q=laptop&status=deployed&tenant={self.tenant.pk}")
        self.assertEqual(context["suffix_scoped"], "&q=laptop&status=deployed")
        self.assertEqual(context["scoped_notice"], "&scope_notice=filters")

    def test_previous_switch_parameters_and_pagination_never_carry_over(self):
        context = self._context(f"/assets/assets/?switch_tenant={self.tenant.pk}&q=laptop&page=3")

        self.assertEqual(context["suffix"], "&q=laptop")
        self.assertEqual(context["suffix_scoped"], "&q=laptop")

    def test_scoped_switch_without_tenant_filter_has_no_notice(self):
        context = self._context("/assets/assets/?q=laptop")

        self.assertEqual(context["suffix_scoped"], "&q=laptop")
        self.assertEqual(context["scoped_notice"], "")

    def test_object_page_switch_lands_on_the_model_list(self):
        context = self._context("/assets/assets/1/")

        self.assertEqual(context["base_url"], reverse("assets:asset_list"))
        self.assertEqual(context["suffix"], "")
        self.assertEqual(context["suffix_scoped"], "")
        self.assertEqual(context["scoped_notice"], "")

    def test_object_page_query_string_does_not_carry_over(self):
        context = self._context("/assets/assets/1/?tenant=5")

        self.assertEqual(context["base_url"], reverse("assets:asset_list"))
        self.assertEqual(context["suffix"], "")
        self.assertEqual(context["scoped_notice"], "")

    def test_unresolvable_object_page_lands_on_the_dashboard(self):
        request = self.factory.get("/somewhere/1/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(kwargs={"pk": "1"}, func=lambda: None)

        context = tenant_switcher_processor(request)["scope_switch"]

        # The object URL cannot be proven to render under the new scope, so the
        # switch must not repeat it: the dashboard renders under any scope.
        self.assertEqual(context["base_url"], reverse("dashboard"))

    def test_notice_marker_never_carries_over(self):
        context = self._context(
            f"/assets/assets/?q=laptop&tenant={self.tenant.pk}&scope_notice=filters&switch_all_accessible=1"
        )

        self.assertNotIn("scope_notice", context["suffix"])
        self.assertNotIn("scope_notice", context["suffix_scoped"])
        self.assertEqual(context["scoped_notice"], "&scope_notice=filters")

    def test_anonymous_request_has_no_switch_context(self):
        context = tenant_switcher_processor(SimpleNamespace(user=AnonymousUser()))["scope_switch"]

        self.assertEqual(context, {})
