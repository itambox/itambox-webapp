import sys
import types
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import resolve, reverse
from django.utils.html import escape
from rest_framework import status
from rest_framework.test import APITestCase

from core.templatetags.plugins import plugin_template_content
from itambox.context_processors import plugin_diagnostics_processor
from itambox.plugins import PluginConfig
from itambox.plugins.utils import deep_merge, load_plugins
from itambox.plugins.views import PluginTemplateContent
from itambox.registry import registry


class DummySettings:
    def __init__(self):
        self.INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes"]
        self.MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
        self.PLUGINS = []
        self.PLUGINS_CONFIG = {}
        self.PLUGINS_RESOLVED_CONFIG = {}
        self.PLUGINS_ACTIVE = []
        self.PLUGINS_DIAGNOSTICS = []


def _plugin_module(name, config_cls):
    module = types.ModuleType(name)
    module.config = config_cls
    sys.modules[name] = module
    return module


def _api_compatible_config(plugin_name, **attrs):
    return type(
        "MockPluginConfig",
        (PluginConfig,),
        {
            "__module__": plugin_name,
            "name": plugin_name,
            "verbose_name": plugin_name,
            "path": str(Path(__file__).resolve().parent),
            "min_plugin_api_version": "1.0",
            "max_plugin_api_version": "1.0",
            **attrs,
        },
    )


class PluginLoaderTestCase(SimpleTestCase):
    def test_deep_merge(self):
        dict1 = {"a": 1, "b": {"c": 3}}
        dict2 = {"b": {"d": 4}, "e": 5}
        expected = {"a": 1, "b": {"c": 3, "d": 4}, "e": 5}
        self.assertEqual(deep_merge(dict1, dict2), expected)

    def test_successful_loader_registration(self):
        dummy_name = "test_mock_plugin"
        MockPluginConfig = _api_compatible_config(
            dummy_name,
            verbose_name="Mock Plugin",
            default_settings={"setting_a": "default_a", "setting_b": "default_b"},
            required_settings=["setting_req"],
            middleware=["test_mock_plugin.middleware.MockMiddleware"],
            django_apps=["test_mock_plugin.auxiliary"],
        )
        _plugin_module(dummy_name, MockPluginConfig)
        middleware_module = types.ModuleType("test_mock_plugin.middleware")

        class MockMiddleware:
            __slots__ = ("get_response",)

            def __init__(self, get_response):
                self.get_response = get_response

        middleware_module.MockMiddleware = MockMiddleware
        sys.modules["test_mock_plugin.middleware"] = middleware_module

        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]
        dummy_settings.PLUGINS_CONFIG = {dummy_name: {"setting_req": "val_req", "setting_b": "val_b_overridden"}}

        load_plugins(dummy_settings)

        self.assertIn(f"{dummy_name}.{MockPluginConfig.__name__}", dummy_settings.INSTALLED_APPS)
        self.assertIn("test_mock_plugin.auxiliary", dummy_settings.INSTALLED_APPS)
        self.assertIn("test_mock_plugin.middleware.MockMiddleware", dummy_settings.MIDDLEWARE)
        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [dummy_name])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS, [])

        load_plugins(dummy_settings)
        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [dummy_name])
        self.assertEqual(dummy_settings.INSTALLED_APPS.count(f"{dummy_name}.{MockPluginConfig.__name__}"), 1)
        self.assertEqual(dummy_settings.MIDDLEWARE.count("test_mock_plugin.middleware.MockMiddleware"), 1)

        resolved = dummy_settings.PLUGINS_RESOLVED_CONFIG[dummy_name]
        self.assertEqual(resolved["setting_a"], "default_a")
        self.assertEqual(resolved["setting_b"], "val_b_overridden")
        self.assertEqual(resolved["setting_req"], "val_req")

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]
        sys.modules.pop("test_mock_plugin.middleware", None)

    def test_improperly_configured_missing_required(self):
        dummy_name = "test_mock_plugin_missing"
        MockPluginConfig = _api_compatible_config(
            dummy_name,
            verbose_name="Mock Plugin Missing",
            required_settings=["crucial_setting"],
        )
        _plugin_module(dummy_name, MockPluginConfig)

        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]
        dummy_settings.PLUGINS_CONFIG = {}

        load_plugins(dummy_settings)

        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [])
        diagnostic = dummy_settings.PLUGINS_DIAGNOSTICS[0]
        self.assertEqual(diagnostic["plugin"], dummy_name)
        self.assertEqual(diagnostic["activation_state"], "disabled")
        self.assertEqual(diagnostic["source"], "settings.PLUGINS")
        self.assertEqual(diagnostic["failure_class"], "ImproperlyConfigured")

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]

    def test_missing_plugin_config_object_is_disabled(self):
        dummy_name = "test_mock_plugin_missing_config"
        sys.modules[dummy_name] = types.ModuleType(dummy_name)
        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)

        diagnostic = dummy_settings.PLUGINS_DIAGNOSTICS[0]
        self.assertEqual(diagnostic["failure_class"], "ImproperlyConfigured")
        self.assertEqual(diagnostic["stage"], "configuration")
        del sys.modules[dummy_name]

    def test_import_failure_is_disabled_without_aborting_startup(self):
        dummy_name = "test_mock_plugin_not_importable"
        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)
        load_plugins(dummy_settings)

        diagnostic = dummy_settings.PLUGINS_DIAGNOSTICS[0]
        self.assertEqual(diagnostic["failure_class"], "ModuleNotFoundError")
        self.assertEqual(diagnostic["stage"], "import")
        self.assertEqual(len(dummy_settings.PLUGINS_DIAGNOSTICS), 1)

    def test_loader_min_version_compatible(self):
        dummy_name = "test_mock_plugin_min_ok"
        MockPluginConfig = _api_compatible_config(
            dummy_name,
            verbose_name="Mock Plugin Min OK",
            min_version="1.0.0-alpha",
        )
        _plugin_module(dummy_name, MockPluginConfig)

        dummy_settings = DummySettings()
        dummy_settings.VERSION = "1.0.0-alpha"
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)
        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [dummy_name])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS, [])

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]

    def test_loader_min_version_incompatible(self):
        dummy_name = "test_mock_plugin_min_fail"
        MockPluginConfig = _api_compatible_config(
            dummy_name,
            verbose_name="Mock Plugin Min Fail",
            min_version="1.1.0",
        )
        _plugin_module(dummy_name, MockPluginConfig)

        dummy_settings = DummySettings()
        dummy_settings.VERSION = "1.0.0-alpha"
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)
        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS[0]["compatibility"], "incompatible-product")

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]

    def test_loader_max_version_incompatible(self):
        dummy_name = "test_mock_plugin_max_fail"
        config_cls = _api_compatible_config(dummy_name, max_version="0.9.0")
        _plugin_module(dummy_name, config_cls)
        dummy_settings = DummySettings()
        dummy_settings.VERSION = "1.0.0-alpha"
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)

        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS[0]["compatibility"], "incompatible-product")
        del sys.modules[dummy_name]

    def test_missing_plugin_api_metadata_is_disabled_without_guessing(self):
        dummy_name = "test_mock_plugin_no_api_metadata"
        config_cls = type("MockPluginConfig", (PluginConfig,), {"name": dummy_name})
        _plugin_module(dummy_name, config_cls)
        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)

        diagnostic = dummy_settings.PLUGINS_DIAGNOSTICS[0]
        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [])
        self.assertEqual(diagnostic["compatibility"], "missing-plugin-api")
        self.assertTrue(diagnostic["value_present"])
        del sys.modules[dummy_name]

    def test_plugin_config_name_must_match_package(self):
        dummy_name = "test_mock_plugin_wrong_name"
        config_cls = _api_compatible_config(dummy_name, name="other_plugin")
        _plugin_module(dummy_name, config_cls)
        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)

        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS[0]["failure_class"], "ImproperlyConfigured")
        del sys.modules[dummy_name]

    def test_plugin_api_version_is_independent_from_product_version(self):
        dummy_name = "test_mock_plugin_api_version"
        config_cls = _api_compatible_config(
            dummy_name,
            min_version="9.0.0",
            max_version="9.0.0",
        )
        _plugin_module(dummy_name, config_cls)
        dummy_settings = DummySettings()
        dummy_settings.VERSION = "9.0.0"
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)

        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [dummy_name])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS, [])
        del sys.modules[dummy_name]

    def test_incompatible_plugin_api_version_is_disabled(self):
        dummy_name = "test_mock_plugin_api_version_fail"
        config_cls = _api_compatible_config(
            dummy_name,
            min_plugin_api_version="2.0",
            max_plugin_api_version="2.0",
        )
        _plugin_module(dummy_name, config_cls)
        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)

        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS[0]["compatibility"], "incompatible-plugin-api")
        del sys.modules[dummy_name]

    def test_one_failed_plugin_does_not_disable_another(self):
        good_name = "test_mock_plugin_good"
        bad_name = "test_mock_plugin_bad"
        good_config = _api_compatible_config(good_name)

        class BadMenu:
            pass

        def broken_ready(self):
            registry.register_plugin_menu(BadMenu)
            registry.register_plugin_viewset(self.name, "broken", object)
            raise ValueError("bad <script>alert('secret-value')</script>")

        bad_middleware_module = types.ModuleType("test_mock_plugin_bad.middleware")

        class BadMiddleware:
            __slots__ = ("get_response",)

            def __init__(self, get_response):
                self.get_response = get_response

        bad_middleware_module.BadMiddleware = BadMiddleware
        sys.modules["test_mock_plugin_bad.middleware"] = bad_middleware_module
        bad_config = _api_compatible_config(
            bad_name,
            ready=broken_ready,
            middleware=["test_mock_plugin_bad.middleware.BadMiddleware"],
        )
        _plugin_module(good_name, good_config)
        _plugin_module(bad_name, bad_config)
        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [good_name, bad_name]
        dummy_settings.PLUGINS_CONFIG = {bad_name: {"secret": "secret-value"}}

        load_plugins(dummy_settings)
        bad_instance = bad_config(bad_name, sys.modules[bad_name])
        bad_instance.ready()

        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [good_name])
        diagnostic = dummy_settings.PLUGINS_DIAGNOSTICS[0]
        self.assertEqual(diagnostic["plugin"], bad_name)
        self.assertEqual(diagnostic["failure_class"], "ValueError")
        self.assertNotIn("secret-value", diagnostic["error"])
        self.assertNotIn("test_mock_plugin_bad.middleware.BadMiddleware", dummy_settings.MIDDLEWARE)
        self.assertNotIn(BadMenu, registry.get_plugin_menus())
        self.assertNotIn(bad_name, registry.get_plugin_viewsets())
        del sys.modules[good_name]
        del sys.modules[bad_name]
        del sys.modules["test_mock_plugin_bad.middleware"]

    def test_malformed_middleware_is_disabled(self):
        dummy_name = "test_mock_plugin_bad_middleware"
        config_cls = _api_compatible_config(dummy_name, middleware=[object()])
        _plugin_module(dummy_name, config_cls)
        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]

        load_plugins(dummy_settings)

        self.assertEqual(dummy_settings.PLUGINS_ACTIVE, [])
        self.assertEqual(dummy_settings.PLUGINS_DIAGNOSTICS[0]["stage"], "middleware")
        del sys.modules[dummy_name]


class TemplateTagTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self._orig_contents = registry._plugin_template_contents.copy()
        self._orig_sources = registry._plugin_template_content_sources.copy()
        registry._plugin_template_contents.clear()
        registry._plugin_template_content_sources.clear()

    def tearDown(self):
        registry._plugin_template_contents = self._orig_contents
        registry._plugin_template_content_sources = self._orig_sources
        super().tearDown()

    def test_template_tag_rendering_and_error_handling(self):
        class GoodContent(PluginTemplateContent):
            def left_panel(self):
                return "<div>Good Injection</div>"

        class BrokenContent(PluginTemplateContent):
            def left_panel(self):
                raise ValueError("Simulated template error")

        registry.register_plugin_template_content("assets.asset", GoodContent)
        registry.register_plugin_template_content("assets.asset", BrokenContent)

        context = {"request": None}
        rendered = plugin_template_content(context, "assets.asset", "left_panel", None)

        self.assertIn("<div>Good Injection</div>", rendered)
        self.assertIn(
            "<!-- Error rendering plugin template content class 'BrokenContent' for position 'left_panel': Simulated template error -->",
            rendered,
        )


class PluginDiagnosticsSurfaceTest(SimpleTestCase):
    def test_diagnostic_is_visible_and_html_escaped(self):
        diagnostic = {
            "plugin": "unsafe_plugin",
            "failure_class": "ValueError",
            "stage": "ready",
            "compatibility": "compatible",
            "activation_mode": "opt-in",
            "activation_state": "disabled",
            "activation_source": "settings.PLUGINS",
            "value_present": True,
            "error": escape("bad <script>alert('secret')</script>"),
        }
        with self.settings(PLUGINS_DIAGNOSTICS=[diagnostic]):
            context = plugin_diagnostics_processor(None)
            rendered = render_to_string("global_includes/_plugin_diagnostics.html", context)

        self.assertIn("unsafe_plugin", rendered)
        self.assertIn("plugin-api-experimental-warning", rendered)
        self.assertIn("ValueError", rendered)
        self.assertIn("settings.PLUGINS", rendered)
        self.assertIn("disabled", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)


class PluginAPITestCase(APITestCase):
    def test_api_route_resolution(self):
        if "itambox_esign" in settings.PLUGINS:
            url = "/api/plugins/itambox_esign/"
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["status"], "active")
            self.assertEqual(response.data["message"], "DocuSign integration plugin API is online.")

    def test_ui_route_resolution(self):
        if "itambox_esign" in settings.PLUGINS:
            url = reverse("plugins:itambox_esign:dashboard")
            self.assertEqual(url, "/plugins/itambox_esign/dashboard/")
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_302_FOUND)
