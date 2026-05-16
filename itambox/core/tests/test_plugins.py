import sys
import types
from django.test import SimpleTestCase, TestCase
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse, resolve
from django.conf import settings
from rest_framework.test import APITestCase
from rest_framework import status

from itambox.plugins.utils import load_plugins, deep_merge
from itambox.plugins import PluginConfig
from itambox.plugins.views import PluginTemplateContent
from itambox.registry import registry
from core.templatetags.plugins import plugin_template_content

class DummySettings:
    def __init__(self):
        self.INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes']
        self.MIDDLEWARE = ['django.middleware.common.CommonMiddleware']
        self.PLUGINS = []
        self.PLUGINS_CONFIG = {}
        self.PLUGINS_RESOLVED_CONFIG = {}

class PluginLoaderTestCase(SimpleTestCase):
    def test_deep_merge(self):
        dict1 = {'a': 1, 'b': {'c': 3}}
        dict2 = {'b': {'d': 4}, 'e': 5}
        expected = {'a': 1, 'b': {'c': 3, 'd': 4}, 'e': 5}
        self.assertEqual(deep_merge(dict1, dict2), expected)

    def test_successful_loader_registration(self):
        dummy_name = 'test_mock_plugin'
        dummy_plugin = types.ModuleType(dummy_name)

        class MockPluginConfig(PluginConfig):
            name = dummy_name
            verbose_name = 'Mock Plugin'
            default_settings = {'setting_a': 'default_a', 'setting_b': 'default_b'}
            required_settings = ['setting_req']
            middleware = ['test_mock_plugin.middleware.MockMiddleware']
            django_apps = ['test_mock_plugin.auxiliary']

        dummy_plugin.config = MockPluginConfig
        sys.modules[dummy_name] = dummy_plugin

        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]
        dummy_settings.PLUGINS_CONFIG = {
            dummy_name: {
                'setting_req': 'val_req',
                'setting_b': 'val_b_overridden'
            }
        }

        load_plugins(dummy_settings)

        self.assertIn(f'{dummy_name}.{MockPluginConfig.__name__}', dummy_settings.INSTALLED_APPS)
        self.assertIn('test_mock_plugin.auxiliary', dummy_settings.INSTALLED_APPS)
        self.assertIn('test_mock_plugin.middleware.MockMiddleware', dummy_settings.MIDDLEWARE)

        resolved = dummy_settings.PLUGINS_RESOLVED_CONFIG[dummy_name]
        self.assertEqual(resolved['setting_a'], 'default_a')
        self.assertEqual(resolved['setting_b'], 'val_b_overridden')
        self.assertEqual(resolved['setting_req'], 'val_req')

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]

    def test_improperly_configured_missing_required(self):
        dummy_name = 'test_mock_plugin_missing'
        dummy_plugin = types.ModuleType(dummy_name)

        class MockPluginConfig(PluginConfig):
            name = dummy_name
            verbose_name = 'Mock Plugin Missing'
            required_settings = ['crucial_setting']

        dummy_plugin.config = MockPluginConfig
        sys.modules[dummy_name] = dummy_plugin

        dummy_settings = DummySettings()
        dummy_settings.PLUGINS = [dummy_name]
        dummy_settings.PLUGINS_CONFIG = {}

        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_plugins(dummy_settings)

        self.assertIn("requires setting 'crucial_setting'", str(ctx.exception))

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]

    def test_loader_min_version_compatible(self):
        dummy_name = 'test_mock_plugin_min_ok'
        dummy_plugin = types.ModuleType(dummy_name)

        class MockPluginConfig(PluginConfig):
            name = dummy_name
            verbose_name = 'Mock Plugin Min OK'
            min_version = '1.0.0-alpha'

        dummy_plugin.config = MockPluginConfig
        sys.modules[dummy_name] = dummy_plugin

        dummy_settings = DummySettings()
        dummy_settings.VERSION = '1.0.0-alpha'
        dummy_settings.PLUGINS = [dummy_name]

        # Should load without raising ImproperlyConfigured
        load_plugins(dummy_settings)

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]

    def test_loader_min_version_incompatible(self):
        dummy_name = 'test_mock_plugin_min_fail'
        dummy_plugin = types.ModuleType(dummy_name)

        class MockPluginConfig(PluginConfig):
            name = dummy_name
            verbose_name = 'Mock Plugin Min Fail'
            min_version = '1.1.0'

        dummy_plugin.config = MockPluginConfig
        sys.modules[dummy_name] = dummy_plugin

        dummy_settings = DummySettings()
        dummy_settings.VERSION = '1.0.0-alpha'
        dummy_settings.PLUGINS = [dummy_name]

        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_plugins(dummy_settings)
        self.assertIn("requires minimum ITAMbox version", str(ctx.exception))

        if dummy_name in sys.modules:
            del sys.modules[dummy_name]


class TemplateTagTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self._orig_contents = registry._plugin_template_contents.copy()
        registry._plugin_template_contents.clear()

    def tearDown(self):
        registry._plugin_template_contents = self._orig_contents
        super().tearDown()

    def test_template_tag_rendering_and_error_handling(self):
        class GoodContent(PluginTemplateContent):
            def left_panel(self):
                return "<div>Good Injection</div>"

        class BrokenContent(PluginTemplateContent):
            def left_panel(self):
                raise ValueError("Simulated template error")

        registry.register_plugin_template_content('assets.asset', GoodContent)
        registry.register_plugin_template_content('assets.asset', BrokenContent)

        context = {'request': None}
        rendered = plugin_template_content(context, 'assets.asset', 'left_panel', None)

        self.assertIn("<div>Good Injection</div>", rendered)
        self.assertIn("<!-- Error rendering plugin template content class 'BrokenContent' for position 'left_panel': Simulated template error -->", rendered)


class PluginAPITestCase(APITestCase):
    def test_api_route_resolution(self):
        if 'itambox_esign' in settings.PLUGINS:
            url = '/api/plugins/itambox_esign/'
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['status'], 'active')
            self.assertEqual(response.data['message'], 'DocuSign integration plugin API is online.')

    def test_ui_route_resolution(self):
        if 'itambox_esign' in settings.PLUGINS:
            url = reverse('plugins:itambox_esign:dashboard')
            self.assertEqual(url, '/plugins/itambox_esign/dashboard/')
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_302_FOUND)

    def test_graphql_dynamic_composition(self):
        from core.schema import schema
        self.assertIn('docusign_status', schema.query._meta.fields)
