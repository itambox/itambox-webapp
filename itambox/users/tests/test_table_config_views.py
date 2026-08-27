from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from users.models import UserPreference
from users.table_config_views import table_config

User = get_user_model()


class TableConfigViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="table-config-user", password="password123")
        self.url = reverse("table_config", kwargs={"model_name": "assets.AssetTable"})

    def test_route_contract_and_owner(self):
        self.assertEqual(self.url, "/tables/config/assets.AssetTable/")
        self.assertIs(resolve(self.url).func, table_config)
        self.assertEqual(table_config.__module__, "users.table_config_views")

    def test_login_is_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_template_context_and_preference_lookup_contract(self):
        prefs = UserPreference.objects.create(
            user=self.user,
            data={"tables": {"assets": {"AssetTable": {"columns": ["name", "asset_tag"]}}}},
        )
        self.client.force_login(self.user)
        template = mock.Mock()
        template.render.return_value = "rendered"

        with mock.patch("users.table_config_views.get_template", return_value=template) as get_template:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"rendered")
        get_template.assert_called_once_with("core/includes/table_config_modal.html")
        context, request = template.render.call_args.args
        self.assertEqual(context["table_name"], "assets.AssetTable")
        self.assertEqual(context["table_verbose_name"], "Assets")
        self.assertEqual([value for value, _label in context["form"].fields["columns"].choices], ["name", "asset_tag"])
        self.assertEqual(request.user, self.user)
        self.assertEqual(UserPreference.objects.get(user=self.user).pk, prefs.pk)
