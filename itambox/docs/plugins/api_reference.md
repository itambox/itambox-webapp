# ITAMbox Plugins API Reference

This document provides a detailed reference for all base classes and registry utilities available to plugin developers.

---

## PluginConfig

`PluginConfig` subclasses Django's `AppConfig` and must be defined in the plugin's `__init__.py` (assigned to the variable `config`).

### Configuration Attributes

- `default_settings` (dict): A dictionary of default configuration settings for the plugin.
- `required_settings` (list): A list of settings keys that the user *must* configure in `settings.PLUGINS_CONFIG`.
- `middleware` (list): Django middleware class strings to dynamically inject into settings `MIDDLEWARE`.
- `django_apps` (list): Additional third-party or local Django apps that must be prepended to `INSTALLED_APPS` (e.g. dependencies).

### Metadata Attributes

- `version` (str): The current version string of the plugin.
- `author` (str): Author's name.
- `author_email` (str): Author's email address.
- `base_url` (str): Custom base routing slug for web/REST API endpoints. Defaults to the plugin name.
- `min_version` (str): Minimum required ITAMbox version (e.g. `'1.0.0'`). Checked at startup.
- `max_version` (str): Maximum supported ITAMbox version. Checked at startup.
- `graphql_schema` (str): Dotted path to the python module exporting Query and Mutation classes to extend the GraphQL schema (e.g., `'itambox_esign.graphql.schema'`).

---

## PluginModel

`PluginModel` is an abstract Django base model that plugins should subclass instead of standard `models.Model`.

```python
from itambox.plugins.models import PluginModel

class MyPluginModel(PluginModel):
    # Field definitions...
```

By inheriting from `PluginModel`, your models automatically gain support for:
- **Tenant Scoping**: Native scoping under tenant instances.
- **Journaling**: Attaching timeline notes and user logs.
- **Tagging**: Support for custom metadata tags.
- **Changelogging**: Built-in tracking of database modifications.
- **Cloning & Exporting**: Support for the template export system.

---

## Registry Singleton

`itambox.registry.registry` is a central in-memory store for plugin extensions. The following methods are commonly used inside the `ready()` method of your `PluginConfig`:

### `register_plugin_template_content(model, content_class)`
Injects custom template UI blocks.
- `model` (str): The low-cased label of the model to target (e.g. `'assets.asset'`).
- `content_class` (type): A subclass of `PluginTemplateContent`.

### `register_plugin_viewset(plugin_name, prefix, viewset, basename)`
Registers a REST API endpoint.
- `plugin_name` (str): Name of the registering plugin.
- `prefix` (str): API route path.
- `viewset` (ViewSet/str): Viewset class or importable dotted string path.
- `basename` (str): Basename for URL reversing.

### `register_plugin_menu(menu_cls)`
Registers sidebar menu structures.
- `menu_cls` (type): A subclass of `PluginNavigationMenu`.

### `register_plugin_menu_item(item_cls)`
Registers a standalone sidebar item.
- `item_cls` (type): A subclass of `PluginNavigationItem`.
