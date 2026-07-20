# Getting Started with ITAMbox Plugins

!!! warning "Status: Beta"
    The plugin system is under active development. APIs may change between minor releases.
    There is no stable production compatibility contract yet. Pin both ITAMbox and plugin revisions, and test each combination in a non-production environment before deployment.

ITAMbox features a powerful, extensible plugin system modeled after **NetBox**. This allows developers to extend the core functionality of ITAMbox without modifying the core codebase.

---

## What Can Plugins Do?

Plugins can hook into several parts of the ITAMbox application:

- **Custom Models**: Create database models with built-in auditing, tagging, and journaling.
- **Web UI & API Endpoints**: Add custom views, routes, and REST API/GraphQL endpoints.
- **Sidebar & Menus**: Inject items into the main sidebar menu.
- **Template Injection**: Dynamically inject custom HTML cards, buttons, or assets into core detail pages.
- **Middleware**: Inject custom middlewares into the request/response lifecycle.

---

## Anatomy of a Plugin

An ITAMbox plugin is a standard Python package containing a Django app, with a few custom hooks. Here is the minimal structure of a plugin named `itambox_esign`:

```text
itambox_esign/
├── __init__.py           # Declares PluginConfig class
├── urls.py               # Web UI routes (optional)
├── navigation.py         # Sidebar menu registration (optional)
├── template_content.py   # Page template injections (optional)
├── models.py             # Database models (optional)
├── graphql/
│   ├── __init__.py
│   └── schema.py         # GraphQL schema extension (optional)
└── api/
    ├── __init__.py
    └── views.py          # REST API viewsets (optional)
```

---

## 1. Creating the PluginConfig

The entry point of any plugin is its `__init__.py` which must subclass `PluginConfig` from `itambox.plugins`.

Create `itambox_esign/__init__.py`:

```python
from itambox.plugins import PluginConfig

class EsignPluginConfig(PluginConfig):
    name = 'itambox_esign'
    verbose_name = 'DocuSign Integration'
    version = '1.0.0'
    author = 'DocuSign Dev Team'
    author_email = 'dev@docusign.com'
    min_version = '1.0.0-alpha'  # Version constraint checks
    graphql_schema = 'itambox_esign.graphql.schema'  # Optional GraphQL hook

    required_settings = ['DOCUSIGN_API_KEY']
    default_settings = {
        'DOCUSIGN_API_KEY': None,
        'DOCUSIGN_SANDBOX': True,
    }

    def ready(self):
        super().ready()
        # Custom registration logic goes here
        # (e.g. registering template injections, viewsets, menus)

config = EsignPluginConfig
```

---

## 2. Registering the Plugin

To enable your plugin, add its package name to the `PLUGINS` list in `core/settings/base.py`:

```python
PLUGINS = [
    'itambox_esign',
]
```

If your plugin requires custom settings, configure them under `PLUGINS_CONFIG`:

```python
PLUGINS_CONFIG = {
    'itambox_esign': {
        'DOCUSIGN_API_KEY': 'your-api-key-here',
        'DOCUSIGN_SANDBOX': True,
    }
}
```

These settings are deep-merged with your plugin's `default_settings` and made available at runtime via `settings.PLUGINS_RESOLVED_CONFIG['itambox_esign']`.
