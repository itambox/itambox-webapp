"""Changelog detail responsive/theme render contracts (Issue #263).

The changelog detail view must stack its modules vertically at mobile
breakpoints (change details, difference, pre-change data, post-change
data) while keeping the side-by-side comparison on wide screens, and it
must never use the legacy `col col-md-*` split that forced two narrow
columns on phones.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.client import RequestFactory
from django.urls import reverse

from assets.models import AssetRole, AssetType, Manufacturer
from core.models import ObjectChange
from itambox.middleware import CurrentUserMiddleware

User = get_user_model()


class ObjectChangeDetailResponsiveRenderTests(TestCase):
    """Render contract for the responsive changelog detail layout."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="chg-responsive",
            password="x",
            is_superuser=True,
        )

    def setUp(self):
        self.factory = RequestFactory()

    def _make_change(self):
        mfr = Manufacturer.objects.create(name="CHG Mfr", slug="chg-mfr")
        role = AssetRole.objects.create(name="CHG Role", slug="chg-role")
        return AssetType.objects.create(
            manufacturer=mfr,
            model="CHG Laptop",
            slug="chg-laptop",
            asset_role=role,
        )

    def _render_detail(self):
        # ChangeLoggingMixin records the change from request context; run the
        # middleware chain exactly like the existing objectchange tests (the
        # request must be active BEFORE the model is created).
        request = self.factory.get("/")
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)
        asset_type = self._make_change()
        middleware.process_response(request, None)
        self.client.force_login(self.user)
        change = ObjectChange.objects.filter(changed_object_id=asset_type.pk).latest("time")
        return self.client.get(reverse("objectchange", args=[change.pk]))

    def test_modules_use_full_width_mobile_columns(self):
        response = self._render_detail()
        self.assertEqual(response.status_code, 200)
        # Mobile-first: every module is col-12 (full width) below md.
        self.assertContains(response, 'class="col-12 col-md-5"')
        self.assertContains(response, 'class="col-12 col-md-7"')
        self.assertEqual(response.content.count(b'class="col-12 col-md-6"'), 2)

    def test_legacy_half_width_columns_are_gone(self):
        response = self._render_detail()
        self.assertNotContains(response, 'class="col col-md-')

    def test_module_order_is_details_difference_pre_post(self):
        response = self._render_detail()
        html = response.content.decode("utf-8")
        order = [
            html.index("Change Details"),
            html.index("Difference"),
            html.index("Pre-Change Data"),
            html.index("Post-Change Data"),
        ]
        self.assertEqual(order, sorted(order), "changelog modules must render in a fixed vertical order")
